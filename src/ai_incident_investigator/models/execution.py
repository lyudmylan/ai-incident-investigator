"""Execution contracts for the v5 flag-toggle pilot (epic #60, issue #64).

Contracts only: nothing in this module can reach a flag system. The
policy gate (#65), the dry-run executor (#66), and the adapter (#67)
consume these types unchanged. Decisions and rationale live in
docs/execution_design.md; the generated schema in
docs/execution_contract.md.

Safety properties carried by the schema itself:

- `FlagToggleRequest.method` is Literal["PATCH"] and the route is derived
  from validated name segments - no other request is representable
  (the publish client's derived-route pattern).
- `ApprovalPolicy.production` has a schema floor of 2: a configuration in
  which a single individual can green-light a production-tier action is
  not representable (owner decision, 2026-07-15).
- The executor credential is an env-var NAME with its own default,
  refused if it collides with the publish or LLM credentials; collection
  config refuses it from the other side (the #54 pattern).
"""

import re
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from ai_incident_investigator.models.common import config_leaves, looks_like_credential_key

DEFAULT_TOKEN_ENV = "FLAG_TOGGLE_TOKEN"

# Env-var names of OTHER credentials this one must never alias. The publish
# string is duplicated from publish.github_issue.DEFAULT_TOKEN_ENV (models/
# must not import publish/); a cross-check test asserts they stay equal.
_FOREIGN_TOKEN_ENVS = ("GITHUB_PUBLISH_TOKEN", "ANTHROPIC_API_KEY")

# Matched with fullmatch: `$` would accept a trailing newline, which must
# never reach the derived route.
_ENV_NAME_PATTERN = re.compile(r"[a-z][a-z0-9-]{0,63}")
_FLAG_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_TOKEN_ENV_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{2,63}")

_ENV_NAME_RULE = "lowercase letters, digits, and hyphens, starting with a letter (max 64 chars)"
_FLAG_KEY_RULE = (
    "letters, digits, dots, underscores, and hyphens, starting alphanumeric (max 128 chars)"
)


class ExecutionConfigError(Exception):
    """The executor config is missing or unusable."""


class EnvironmentTier(StrEnum):
    """Risk tier of a flag environment; the approval policy keys on it."""

    SANDBOX = "sandbox"
    STAGING = "staging"
    PRODUCTION = "production"


PILOT_LIVE_TIERS: frozenset[EnvironmentTier] = frozenset(
    {EnvironmentTier.SANDBOX, EnvironmentTier.STAGING}
)
"""Tiers a live toggle may target during the pilot. Production entries may
exist in the allowlist (so plans and dry-runs can name them) but the live
path refuses them until the pilot proves out (epic #60)."""


class ApprovalPolicy(BaseModel):
    """Distinct-approver quorum per environment tier.

    Deliberately NOT a role hierarchy (owner decision, 2026-07-15): the
    on-call engineer is authorized to approve; a production-tier action
    additionally needs a second distinct approver. The `ge=2` floor on
    `production` makes the single-individual configuration unrepresentable.
    Identities are claimed, not authenticated - this is a process control
    (docs/execution_design.md, "Honest limitations").
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sandbox: int = Field(default=1, ge=1)
    staging: int = Field(default=1, ge=1)
    production: int = Field(default=2, ge=2)
    invoker_counts_toward_quorum: bool = Field(
        default=True,
        description="whether the identity invoking `execute` may be one of the "
        "counted approvers; the control is the second pair of eyes, not "
        "invoker exclusion - set false for stricter separation of duties",
    )

    def required_for(self, tier: EnvironmentTier) -> int:
        # match, not a dict: mypy enforces exhaustiveness, so a new tier
        # cannot silently become a runtime KeyError inside the policy gate.
        match tier:
            case EnvironmentTier.SANDBOX:
                return self.sandbox
            case EnvironmentTier.STAGING:
                return self.staging
            case EnvironmentTier.PRODUCTION:
                return self.production


class FlagEnvironment(BaseModel):
    """One environment in the allowlist: exact flag keys, nothing else."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(description="environment name; also a URL path segment")
    tier: EnvironmentTier
    flags: list[str] = Field(default_factory=list, description="exact flag keys; no patterns")

    @field_validator("name")
    @classmethod
    def _name_shape(cls, value: str) -> str:
        if not _ENV_NAME_PATTERN.fullmatch(value):
            raise ValueError(f"environment name must be {_ENV_NAME_RULE}")
        return value

    @field_validator("flags")
    @classmethod
    def _flag_shapes(cls, value: list[str]) -> list[str]:
        for flag in value:
            if not _FLAG_KEY_PATTERN.fullmatch(flag):
                raise ValueError(f"flag key {flag!r} must be {_FLAG_KEY_RULE}")
        if len(set(value)) != len(value):
            raise ValueError("flag keys must be unique within an environment")
        return value


class ExecutorConfig(BaseModel):
    """The executor's entire world: one flag service, an allowlist, a policy.

    A flag/environment pair absent from the allowlist is structurally
    unreachable - there is no way to express toggling it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = Field(description="flag service root; the route is derived from it")
    token_env: str = Field(
        default=DEFAULT_TOKEN_ENV,
        description="env var NAME holding the executor credential - its own "
        "token, never shared with collection or publish",
    )
    policy: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
    environments: list[FlagEnvironment] = Field(min_length=1)

    @field_validator("base_url")
    @classmethod
    def _base_url_shape(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value.rstrip("/")

    @field_validator("token_env")
    @classmethod
    def _token_env_shape(cls, value: str) -> str:
        if not _TOKEN_ENV_PATTERN.fullmatch(value):
            raise ValueError("token_env must be an UPPER_SNAKE env var name, not a value")
        if value in _FOREIGN_TOKEN_ENVS:
            raise ValueError(
                f"token_env {value} belongs to another credential; the executor "
                "must have its own token (docs/execution_design.md)"
            )
        return value

    @field_validator("environments")
    @classmethod
    def _unique_names(cls, value: list[FlagEnvironment]) -> list[FlagEnvironment]:
        names = [environment.name for environment in value]
        if len(set(names)) != len(names):
            raise ValueError("environment names must be unique")
        return value

    def environment(self, name: str) -> FlagEnvironment | None:
        return next((e for e in self.environments if e.name == name), None)

    def allows(self, environment_name: str, flag_key: str) -> bool:
        environment = self.environment(environment_name)
        return environment is not None and flag_key in environment.flags


class FlagToggleRequest(BaseModel):
    """The ONLY action the pilot can express: set one allowlisted flag
    on or off in one named environment. Route and verb are fixed; the
    validated segments are the only variable parts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: Literal["PATCH"] = "PATCH"
    environment: str
    flag_key: str
    on: bool

    @field_validator("environment")
    @classmethod
    def _environment_shape(cls, value: str) -> str:
        if not _ENV_NAME_PATTERN.fullmatch(value):
            raise ValueError(f"environment must be {_ENV_NAME_RULE}")
        return value

    @field_validator("flag_key")
    @classmethod
    def _flag_key_shape(cls, value: str) -> str:
        if not _FLAG_KEY_PATTERN.fullmatch(value):
            raise ValueError(f"flag_key must be {_FLAG_KEY_RULE}")
        return value


ExecutionMode = Literal["dry_run", "live"]

ExecutionOutcome = Literal["previewed", "applied", "refused", "failed"]
"""previewed: dry-run printed what would change; applied: the live call
succeeded; refused: a gate said no (the record keeps the reason); failed:
the live call was attempted and did not succeed."""

VerificationOutcome = Literal["not_applicable", "pending", "verified", "unverifiable", "aborted"]
"""Filled by post-execution verification (#68). Dry-runs are
not_applicable; a live execution starts pending; absent signals are
unverifiable, never assumed good; a met abort condition is recorded as
aborted, never silently ignored."""


class ExecutionRecord(BaseModel):
    """One executor decision, written next to the approvals BEFORE success
    is reported (epic #60 hard precondition). Append-only, like approvals.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    executed_by: str = Field(description="identity as claimed; authentication is post-pilot")
    executed_at: AwareDatetime
    mode: ExecutionMode
    action: FlagToggleRequest
    plan_id: str
    step_index: int = Field(ge=0)
    report_sha256: str = Field(
        min_length=64, max_length=64, description="hash of the exact report executed against"
    )
    required_approvals: int = Field(ge=1, description="quorum the tier's policy demanded")
    approvals_satisfied: list[str] = Field(
        description="distinct claimed identities whose valid approvals met the quorum"
    )
    outcome: ExecutionOutcome
    verification: VerificationOutcome
    detail: str | None = Field(default=None, description="refusal reason or failure detail")


class ExecutionsFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    executions: list[ExecutionRecord] = Field(default_factory=list)


def executions_path(report_path: Path) -> Path:
    return report_path.with_suffix(".executions.json")


def _reject_pasted_credentials(node: dict[str, object], path: Path) -> None:
    """A credential-looking VALUE anywhere in the executor config - including
    inside [[environments]] tables - is rejected outright; credentials are
    env-var references, never values (traversal shared with collect/config.py
    via models.common so the two guardrails cannot drift)."""
    for location, key, value in config_leaves(node):
        if looks_like_credential_key(key) and isinstance(value, str):
            raise ExecutionConfigError(
                f"{path}: {location} looks like a credential value. Credentials "
                "must be env-var references (use token_env naming the variable), "
                "never values in the config."
            )


def load_executor_config(path: Path) -> ExecutorConfig:
    if not path.is_file():
        raise ExecutionConfigError(f"executor config not found: {path}")
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ExecutionConfigError(f"executor config {path} is not valid TOML: {exc}") from exc
    _reject_pasted_credentials(data, path)
    return ExecutorConfig.model_validate(data)
