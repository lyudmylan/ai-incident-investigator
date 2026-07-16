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

from ai_incident_investigator.models.common import config_leaves, looks_like_credential_key


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


# Env var names of write-side credentials (publish.github_issue.DEFAULT_TOKEN_ENV,
# models.execution.DEFAULT_TOKEN_ENV; duplicated here because collect/ must not
# import from either module - cross-check tests assert the strings stay equal).
# Collection refusing them keeps every write credential structurally out of
# every read path.
_PUBLISH_TOKEN_ENV = "GITHUB_PUBLISH_TOKEN"
_EXECUTOR_TOKEN_ENV = "FLAG_TOGGLE_TOKEN"
_WRITE_TOKEN_ENVS = {
    _PUBLISH_TOKEN_ENV: "publish",
    _EXECUTOR_TOKEN_ENV: "flag executor",
}


def _reject_pasted_credentials(node: dict[str, Any], path: Path, where: str) -> None:
    """A credential-looking value anywhere in the config - including inside
    arrays of tables - is rejected (traversal shared with the executor config
    via models.common so the two guardrails cannot drift)."""
    for location, key, value in config_leaves(node, where):
        if key.lower().endswith("_env") and isinstance(value, str) and value in _WRITE_TOKEN_ENVS:
            raise CollectError(
                f"{path}: {location} references the {_WRITE_TOKEN_ENVS[value]} credential "
                f"({value}). A write-side token must never be used for "
                "collection; give the read source its own token."
            )
        if looks_like_credential_key(key) and isinstance(value, str):
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
