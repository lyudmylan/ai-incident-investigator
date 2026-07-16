"""Contract tests for the v5 execution models (issue #64).

Nothing here executes anything - these tests pin the SHAPES: the quorum
floor that makes single-individual production execution unrepresentable,
the route-bending rejections, and the credential-isolation constants.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_incident_investigator.collect.config import (
    _EXECUTOR_TOKEN_ENV,
    _PUBLISH_TOKEN_ENV,
    CollectError,
    load_sources_config,
)
from ai_incident_investigator.models.execution import (
    DEFAULT_TOKEN_ENV,
    PILOT_LIVE_TIERS,
    ApprovalPolicy,
    EnvironmentTier,
    ExecutionConfigError,
    ExecutionRecord,
    ExecutionsFile,
    ExecutorConfig,
    FlagEnvironment,
    FlagToggleRequest,
    executions_path,
    load_executor_config,
)
from ai_incident_investigator.publish.github_issue import (
    DEFAULT_TOKEN_ENV as PUBLISH_TOKEN_ENV,
)

ROOT = Path(__file__).resolve().parents[1]

STAGING = FlagEnvironment(name="staging", tier=EnvironmentTier.STAGING, flags=["payment_flag"])


def _config(**overrides: object) -> ExecutorConfig:
    payload: dict[str, object] = {
        "base_url": "https://flags.example",
        "environments": [STAGING.model_dump(mode="json")],
    }
    payload.update(overrides)
    return ExecutorConfig.model_validate(payload)


def test_production_quorum_floor_is_schema_enforced() -> None:
    """The owner decision: a config in which one person can green-light a
    production-tier action must be unrepresentable, not merely invalid."""
    with pytest.raises(ValidationError):
        ApprovalPolicy(production=1)
    with pytest.raises(ValidationError):
        ApprovalPolicy(sandbox=0)
    with pytest.raises(ValidationError):
        ApprovalPolicy(staging=0)


def test_policy_defaults_and_tier_lookup() -> None:
    policy = ApprovalPolicy()
    assert policy.sandbox == 1
    assert policy.staging == 1
    assert policy.production == 2
    assert policy.invoker_counts_toward_quorum is True
    assert policy.required_for(EnvironmentTier.PRODUCTION) == 2
    assert policy.required_for(EnvironmentTier.SANDBOX) == 1


def test_production_is_not_a_pilot_live_tier() -> None:
    assert EnvironmentTier.PRODUCTION not in PILOT_LIVE_TIERS
    assert {EnvironmentTier.SANDBOX, EnvironmentTier.STAGING} == PILOT_LIVE_TIERS


@pytest.mark.parametrize(
    "flag_key",
    [
        "../billing",
        "a/b",
        "a b",
        "",
        "-leading",
        "flags?x=1",
        ".hidden",
        "x" * 129,
        "payment_flag\n",  # $-anchored regexes would accept this; fullmatch must not
    ],
)
def test_flag_keys_that_could_bend_the_route_are_rejected(flag_key: str) -> None:
    with pytest.raises(ValidationError):
        FlagToggleRequest.model_validate(
            {"environment": "staging", "flag_key": flag_key, "on": True}
        )


@pytest.mark.parametrize("environment", ["Prod", "prod/us", "..", "-x", "", "us east", "staging\n"])
def test_environment_names_that_could_bend_the_route_are_rejected(environment: str) -> None:
    with pytest.raises(ValidationError):
        FlagToggleRequest.model_validate(
            {"environment": environment, "flag_key": "payment_flag", "on": False}
        )


@pytest.mark.parametrize("method", ["DELETE", "POST", "GET", "PUT"])
def test_the_only_representable_verb_is_patch(method: str) -> None:
    with pytest.raises(ValidationError):
        FlagToggleRequest.model_validate(
            {"method": method, "environment": "staging", "flag_key": "payment_flag", "on": True}
        )


@pytest.mark.parametrize(
    "token_env",
    ["GITHUB_PUBLISH_TOKEN", "ANTHROPIC_API_KEY", "flag_token", "sk-ant-abc123", "X", "TOKEN_X\n"],
)
def test_token_env_refuses_foreign_credentials_and_non_names(token_env: str) -> None:
    with pytest.raises(ValidationError):
        _config(token_env=token_env)


def test_config_defaults_and_allowlist_lookup() -> None:
    config = _config()
    assert config.token_env == DEFAULT_TOKEN_ENV
    assert config.policy.production == 2
    assert config.allows("staging", "payment_flag")
    assert not config.allows("staging", "unlisted_flag")
    assert not config.allows("unknown-env", "payment_flag")
    assert config.environment("unknown-env") is None


def test_duplicate_environments_and_flags_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _config(
            environments=[
                STAGING.model_dump(mode="json"),
                STAGING.model_dump(mode="json"),
            ]
        )
    with pytest.raises(ValidationError):
        FlagEnvironment(
            name="staging", tier=EnvironmentTier.STAGING, flags=["dup_flag", "dup_flag"]
        )


def test_base_url_must_be_http_and_is_normalized() -> None:
    assert _config(base_url="https://flags.example/").base_url == "https://flags.example"
    with pytest.raises(ValidationError):
        _config(base_url="ftp://flags.example")


def _record_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "executed_by": "lyudmyla",
        "executed_at": datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
        "mode": "dry_run",
        "action": {"environment": "staging", "flag_key": "payment_flag", "on": False},
        "plan_id": "plan_x",
        "step_index": 1,
        "report_sha256": "a" * 64,
        "required_approvals": 2,
        "approvals_satisfied": ["lyudmyla", "peer"],
        "outcome": "previewed",
        "verification": "not_applicable",
    }
    payload.update(overrides)
    return payload


def test_execution_record_shape() -> None:
    record = ExecutionRecord.model_validate(_record_payload())
    assert record.detail is None
    with pytest.raises(ValidationError):
        ExecutionRecord.model_validate(_record_payload(report_sha256="short"))
    with pytest.raises(ValidationError):
        ExecutionRecord.model_validate(_record_payload(step_index=-1))
    with pytest.raises(ValidationError):
        ExecutionRecord.model_validate(_record_payload(outcome="executed"))
    with pytest.raises(ValidationError):
        ExecutionRecord.model_validate({**_record_payload(), "unexpected": "field"})


def test_executions_file_defaults_empty_and_sidecar_path() -> None:
    assert ExecutionsFile().executions == []
    assert executions_path(Path("/x/report.json")) == Path("/x/report.executions.json")


def test_load_executor_config_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "executor.toml"
    path.write_text(
        'base_url = "https://flags.example"\n'
        "[policy]\n"
        "production = 3\n"
        "[[environments]]\n"
        'name = "prod-us"\n'
        'tier = "production"\n'
        'flags = ["payment_flag"]\n'
    )
    config = load_executor_config(path)
    assert config.policy.production == 3
    assert config.allows("prod-us", "payment_flag")


def test_load_executor_config_failures(tmp_path: Path) -> None:
    with pytest.raises(ExecutionConfigError, match="not found"):
        load_executor_config(tmp_path / "missing.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("base_url = ")
    with pytest.raises(ExecutionConfigError, match="not valid TOML"):
        load_executor_config(bad)
    pasted = tmp_path / "pasted.toml"
    pasted.write_text(
        'base_url = "https://flags.example"\n'
        "[service]\n"
        'api_key = "sk-live-abc"\n'
        "[[environments]]\n"
        'name = "staging"\n'
        'tier = "staging"\n'
    )
    with pytest.raises(ExecutionConfigError, match="credential value"):
        load_executor_config(pasted)


def test_example_executor_config_is_valid() -> None:
    config = load_executor_config(ROOT / "examples" / "execute" / "executor.toml")
    assert config.policy.production == 2
    assert config.allows("staging", "payment_enrichment")
    production = config.environment("prod-us")
    assert production is not None
    assert production.tier not in PILOT_LIVE_TIERS


def test_credential_isolation_constants_stay_equal() -> None:
    """collect/ duplicates both write-side env names because it must not
    import publish/ or models.execution; these pins keep them honest."""
    assert _PUBLISH_TOKEN_ENV == PUBLISH_TOKEN_ENV
    assert _EXECUTOR_TOKEN_ENV == DEFAULT_TOKEN_ENV
    assert DEFAULT_TOKEN_ENV != PUBLISH_TOKEN_ENV


def test_collection_refuses_the_executor_credential(tmp_path: Path) -> None:
    """The other direction of isolation: a read source configured with the
    executor's token env is rejected outright."""
    sources = tmp_path / "sources.toml"
    sources.write_text(f'[sentry]\ntoken_env = "{DEFAULT_TOKEN_ENV}"\n')
    with pytest.raises(CollectError, match="references the flag executor credential"):
        load_sources_config(sources)


def test_credential_guardrails_reach_inside_arrays_of_tables(tmp_path: Path) -> None:
    """TOML [[tables]] must not be a blind spot for either config scanner."""
    executor = tmp_path / "executor.toml"
    executor.write_text(
        'base_url = "https://flags.example"\n'
        "[[environments]]\n"
        'name = "staging"\n'
        'tier = "staging"\n'
        'api_key = "sk-live-pasted"\n'
    )
    with pytest.raises(ExecutionConfigError, match="credential value"):
        load_executor_config(executor)

    sources = tmp_path / "sources.toml"
    sources.write_text(f'[sentry]\n[[sentry.endpoints]]\ntoken_env = "{DEFAULT_TOKEN_ENV}"\n')
    with pytest.raises(CollectError, match="references the flag executor credential"):
        load_sources_config(sources)
