"""sources.toml loading.

The framework validates only the [collection] section; each adapter epic owns
and validates its own section schema. One guardrail is framework-level: a
config value that looks like a pasted credential is rejected outright -
credentials are env-var references (`*_env` keys), never values.
"""

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "apikey")


class CollectError(Exception):
    """Collection cannot proceed (bad config, unusable anchor, unwritable target)."""


class CollectionSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    services: list[str] = Field(default_factory=list)
    lookback_minutes: int = 30
    change_lookback_days: int = 7


class SourcesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    collection: CollectionSettings
    sections: dict[str, dict[str, Any]]

    def section(self, name: str) -> dict[str, Any]:
        try:
            return self.sections[name]
        except KeyError:
            raise CollectError(
                f"sources config {self.path} has no [{name}] section, "
                f"which the {name} source requires"
            ) from None

    def has_section(self, name: str) -> bool:
        return name in self.sections

    def resolve_path(self, relative: str) -> Path:
        """Paths in the config are relative to the config file itself."""
        return (self.path.parent / relative).resolve()


def _reject_pasted_credentials(node: dict[str, Any], path: Path, where: str) -> None:
    """Recursive: a credential-looking value anywhere in the config is rejected."""
    for key, value in node.items():
        location = f"{where}.{key}" if where else key
        if isinstance(value, dict):
            _reject_pasted_credentials(value, path, location)
            continue
        looks_secret = any(marker in key.lower() for marker in _SECRET_KEY_MARKERS)
        if looks_secret and not key.lower().endswith("_env") and isinstance(value, str):
            raise CollectError(
                f"{path}: {location} looks like a credential value. "
                "Credentials must be env-var references (use a *_env key naming "
                "the variable), never values in the config."
            )


def load_sources_config(path: Path) -> SourcesConfig:
    if not path.is_file():
        raise CollectError(f"sources config not found: {path}")
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise CollectError(f"sources config {path} is not valid TOML: {exc}") from exc

    collection_raw = data.pop("collection", {})
    stray = sorted(name for name, value in data.items() if not isinstance(value, dict))
    if stray:
        raise CollectError(
            f"{path}: top-level keys {', '.join(stray)} do not belong to any section; "
            "did you mean to put them under [collection] or a source section?"
        )
    sections = {name: value for name, value in data.items() if isinstance(value, dict)}
    _reject_pasted_credentials(sections, path, "")
    return SourcesConfig(
        path=path,
        collection=CollectionSettings.model_validate(collection_raw),
        sections=sections,
    )
