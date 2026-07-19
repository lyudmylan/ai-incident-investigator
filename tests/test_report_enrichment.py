"""investigate --history (issue #89): prior incidents embed in the report
under the invariance guarantee - enrichment may add context, never move a
conclusion."""

import json
from pathlib import Path

import pytest

from ai_incident_investigator.cli import main
from ai_incident_investigator.history import add_entry, load_entries
from ai_incident_investigator.markdown import render_markdown
from ai_incident_investigator.models.common import Source
from ai_incident_investigator.models.execution import ExecutionsFile
from ai_incident_investigator.models.history import HistoryEntry, entry_id_for
from ai_incident_investigator.models.report import InvestigationReport, MitigationOption
from ai_incident_investigator.patterns import enrich_report
from test_patterns import (
    SHA_B,
    evidence,
    execution,
    fingerprint,
    make_report,
    verification_record,
    watched_signal,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "latency_spike.json"
PRIOR_GOLDEN = ROOT / "tests" / "golden" / "collected_demo.json"
EXAMPLE = ROOT / "examples" / "incidents" / "latency_spike"
FIXTURES = ROOT / "tests" / "fixtures" / "llm" / "latency_spike"


def golden_report() -> InvestigationReport:
    return InvestigationReport.model_validate_json(GOLDEN.read_text())


@pytest.fixture()
def seeded_history(tmp_path: Path) -> Path:
    history = tmp_path / "history"
    add_entry(history, PRIOR_GOLDEN)
    return history


def _strip_enrichment(report: InvestigationReport) -> dict[str, object]:
    dumped = report.model_dump(mode="json")
    dumped.pop("prior_incidents")
    for mitigation in dumped["safe_mitigation_options"]:
        mitigation["precedent"] = None
    return dumped


def test_enrichment_is_additive_by_construction(seeded_history: Path) -> None:
    report = golden_report()
    entries, notes = load_entries(seeded_history)
    assert notes == []
    enriched = enrich_report(report, entries)
    assert [m.incident_id for m in enriched.prior_incidents] == ["collected_demo"]
    # everything except prior_incidents and precedent notes is byte-identical
    assert _strip_enrichment(enriched) == _strip_enrichment(report)
    assert enriched.severity == report.severity
    assert enriched.hypotheses == report.hypotheses
    assert enriched.recommended_next_steps == report.recommended_next_steps


def make_entry_with_fix(verified: bool, flag: str = "checkout_enrichment") -> HistoryEntry:
    executions = ExecutionsFile(
        executions=[execution("applied", flag=flag)],
        verifications=(
            [verification_record("verified", execution("applied").executed_at.replace(hour=16))]
            if verified
            else []
        ),
    )
    fp = fingerprint(incident_id="prior_incident", sha=SHA_B, fixes=executions)
    return HistoryEntry(entry_id=entry_id_for(fp), fingerprint=fp)


def option(action: str) -> MitigationOption:
    return MitigationOption(id="mit_1", action=action, rationale="stops the bleeding")


def probe_report() -> InvestigationReport:
    """A report whose fingerprint shares the default booking-service/p95
    pair with the test entries - the gate must pass for annotation tests
    to prove anything."""
    return make_report(
        items=[evidence("e1", Source.METRICS, "booking-service", "p95_latency_ms", 3200.0)],
        watched=[watched_signal("booking-service", "p95_latency_ms", 450.0)],
    )


def test_precedent_notes_follow_the_wording_rule() -> None:
    report = probe_report().model_copy(
        update={
            "safe_mitigation_options": [
                option("disable feature flag checkout_enrichment"),
                option("roll back release 2026.06.01"),
            ]
        }
    )
    enriched = enrich_report(report, [make_entry_with_fix(verified=True)])
    assert enriched.prior_incidents, "the gate must pass for this test to mean anything"
    named, unnamed = enriched.safe_mitigation_options
    assert named.precedent == (
        "precedent: staging/checkout_enrichment -> off on prior_incident "
        "(2026-06-01) verified-recovered that incident"
    )
    assert unnamed.precedent is None

    cautioned = enrich_report(report, [make_entry_with_fix(verified=False)])
    assert cautioned.safe_mitigation_options[0].precedent == (
        "caution: staging/checkout_enrichment -> off on prior_incident "
        "(2026-06-01) was tried and did NOT verify (pending)"
    )


def test_flag_names_match_on_word_boundaries_only() -> None:
    report = probe_report().model_copy(
        update={"safe_mitigation_options": [option("disable checkout_enrichment")]}
    )
    enriched = enrich_report(report, [make_entry_with_fix(verified=True, flag="enrich")])
    assert enriched.prior_incidents, "the gate must pass for this test to mean anything"
    assert enriched.safe_mitigation_options[0].precedent is None  # 'enrich' != '_enrich'


def test_markdown_renders_priors_and_precedent(seeded_history: Path) -> None:
    report = golden_report()
    plain = render_markdown(report)
    assert "## Prior incidents" not in plain

    entries, _ = load_entries(seeded_history)
    enriched = enrich_report(report, entries)
    rendered = render_markdown(enriched)
    assert "## Prior incidents (deterministic pattern matches)" in rendered
    assert "resembles collected_demo" in rendered
    assert "Conclusions above are unchanged by these matches._" in rendered
    # the section sits after next steps, before mitigation options
    assert rendered.index("## Recommended next steps") < rendered.index("## Prior incidents")
    assert rendered.index("## Prior incidents") < rendered.index("## Safe mitigation options")


def test_cli_replay_with_history_end_to_end(
    seeded_history: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "report.json"
    args = [
        "investigate",
        "--incident",
        str(EXAMPLE),
        "--llm",
        "replay",
        "--fixtures-dir",
        str(FIXTURES),
        "--history",
        str(seeded_history),
        "--output",
        str(out),
    ]
    assert main(args) == 0
    err = capsys.readouterr().err
    assert "prior incidents: 1 match(es) from the history" in err
    report = json.loads(out.read_text())
    assert [m["incident_id"] for m in report["prior_incidents"]] == ["collected_demo"]
    assert report["prior_incidents"][0]["score"] == sum(
        f["weight"] for f in report["prior_incidents"][0]["matched"]
    )


def test_cli_history_guards(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "missing"
    args = ["investigate", "--incident", str(EXAMPLE), "--llm", "replay", "--history"]
    assert main([*args, str(missing)]) == 1
    assert "history directory not found" in capsys.readouterr().err

    off = ["investigate", "--incident", str(EXAMPLE), "--history", str(tmp_path)]
    assert main(off) == 2
    assert "facts-only mode has no report" in capsys.readouterr().err
