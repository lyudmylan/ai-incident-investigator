from datetime import UTC
from pathlib import Path

import pytest

from ai_incident_investigator.collect import (
    CollectError,
    CollectionSettings,
    RecordingHTTPClient,
    ReplayHTTPClient,
    collect_package,
    load_sources_config,
)
from ai_incident_investigator.collect.normalize import normalize_level
from ai_incident_investigator.collect.sentry import (
    SentryAlertSource,
    SentryConfig,
    parse_sentry_time,
    sentry_alert_source,
)
from ai_incident_investigator.loading import load_package
from sentry_stub import BASE_URL, DEMO_CONFIG, DEMO_ISSUE_ID, SentryStubHTTP

CONFIG = DEMO_CONFIG
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "http" / "sentry_demo"


def _source(stub: SentryStubHTTP | None = None) -> SentryAlertSource:
    return SentryAlertSource(stub or SentryStubHTTP(), CONFIG, DEMO_ISSUE_ID)


def test_alert_mapping_from_issue_and_event() -> None:
    bundle = _source().fetch_alert()
    alert = bundle.alert
    assert alert.id == "sentry_9101"
    assert alert.title.startswith("EligibilityTimeout")
    assert alert.service == "booking-service"  # service tag wins over project slug
    assert alert.triggered_at.isoformat() == "2026-06-01T14:35:52+00:00"  # event time
    assert alert.severity == "error"
    assert alert.description is not None
    assert "culprit: booking.eligibility.check_eligibility" in alert.description
    assert "https://sentry.stub.local" in alert.description
    assert alert.signal is None and alert.threshold is None  # not derivable: unset


def test_breadcrumb_log_mapping_rules() -> None:
    logs = _source().fetch_alert().logs
    messages = [record.message for record in logs]
    # event record + 3 usable breadcrumbs; the message-less crumb is skipped
    assert len(logs) == 4
    assert messages[0].startswith("EligibilityTimeout")
    assert "[query] eligibility lookup started for booking 84163" in messages
    assert "[retry] eligibility lookup retry (attempt 4 of 5)" in messages
    assert "giving up after 5 attempts" in messages  # no category, no prefix

    naive_crumb = next(r for r in logs if "retry (attempt 4" in r.message)
    assert naive_crumb.timestamp.tzinfo is UTC  # naive wire time -> UTC attached
    assert naive_crumb.level == "WARN"
    critical = next(r for r in logs if "giving up" in r.message)
    assert critical.level == "FATAL"


def test_issue_only_fallback_when_event_unavailable() -> None:
    bundle = _source(SentryStubHTTP(latest_event_status=404)).fetch_alert()
    assert bundle.alert.service == "booking"  # project slug fallback
    assert bundle.alert.triggered_at.isoformat() == "2026-06-01T14:55:46+00:00"  # lastSeen
    assert bundle.logs == []


def test_service_tag_fallback_when_not_configured() -> None:
    source = SentryAlertSource(SentryStubHTTP(), SentryConfig(base_url=BASE_URL), DEMO_ISSUE_ID)
    assert source.fetch_alert().alert.service == "booking"


def test_unusable_issue_is_a_collect_error() -> None:
    class BrokenStub(SentryStubHTTP):
        def get(self, request, auth=None):  # type: ignore[no-untyped-def]
            from ai_incident_investigator.collect.http import HTTPResponse

            return HTTPResponse(status=200, body='{"unexpected": "shape"}')

    with pytest.raises(CollectError, match="not understood"):
        SentryAlertSource(BrokenStub(), CONFIG, DEMO_ISSUE_ID).fetch_alert()


def test_auth_reference_is_plumbed_through() -> None:
    stub = SentryStubHTTP()
    config = SentryConfig(base_url=BASE_URL, token_env="SENTRY_TOKEN")
    SentryAlertSource(stub, config, DEMO_ISSUE_ID).fetch_alert()
    auths = {auth.env_var for _, auth in stub.calls if auth is not None}
    assert auths == {"SENTRY_TOKEN"}


def test_level_normalization_table() -> None:
    assert normalize_level("critical") == "FATAL"
    assert normalize_level("warning") == "WARN"
    assert normalize_level("made-up") == "INFO"
    assert normalize_level(None) == "INFO"


def test_parse_sentry_time_rejects_garbage() -> None:
    with pytest.raises(Exception, match="datetime"):
        parse_sentry_time("not-a-time")


def test_factory_validates_section(tmp_path: Path) -> None:
    good = tmp_path / "sources.toml"
    good.write_text(f'[sentry]\nbase_url = "{BASE_URL}"\nservice_tag = "service"\n')
    source = sentry_alert_source(load_sources_config(good), SentryStubHTTP(), DEMO_ISSUE_ID)
    assert source.fetch_alert().alert.service == "booking-service"

    bad = tmp_path / "bad.toml"
    bad.write_text('[sentry]\nbase_url = "https://x"\nunknown_key = 1\n')
    with pytest.raises(CollectError, match=r"\[sentry\] section is invalid"):
        sentry_alert_source(load_sources_config(bad), SentryStubHTTP(), DEMO_ISSUE_ID)


def test_committed_fixtures_power_a_full_collection(tmp_path: Path) -> None:
    """Replay the committed HTTP fixtures end-to-end into a loadable package."""
    source = SentryAlertSource(ReplayHTTPClient(FIXTURES), CONFIG, DEMO_ISSUE_ID)
    out = tmp_path / "collected"
    report = collect_package(source, [], out, CollectionSettings())

    assert [s.status for s in report.sources] == ["ok"]
    loaded = load_package(out)
    assert loaded.package.alert.id == "sentry_9101"
    assert len(loaded.package.logs) == 4
    assert loaded.package.alert.triggered_at.isoformat() == "2026-06-01T14:35:52+00:00"


def test_fixture_regeneration_matches_committed(tmp_path: Path) -> None:
    """The stub and the committed fixtures cannot drift apart silently."""
    recorder = RecordingHTTPClient(SentryStubHTTP(), tmp_path)
    SentryAlertSource(recorder, CONFIG, DEMO_ISSUE_ID).fetch_alert()
    fresh = {p.name: p.read_text() for p in tmp_path.glob("*.json")}
    committed = {p.name: p.read_text() for p in FIXTURES.glob("*.json")}
    assert fresh == committed
