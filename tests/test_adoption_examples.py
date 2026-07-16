"""The adoption guide's promises, executable (#78)."""

from pathlib import Path

from ai_incident_investigator.collect import ReplayHTTPClient, load_sources_config
from ai_incident_investigator.collect.registry import build_sources

ROOT = Path(__file__).resolve().parents[1]


def test_minimal_example_config_is_anchor_only() -> None:
    """docs/adoption.md step 1: the alert anchor is the ONLY required
    source, and the committed minimal template is exactly that - it must
    load and build zero optional adapters."""
    config = load_sources_config(ROOT / "examples" / "collect" / "sources.minimal.toml")
    alert_source, adapters = build_sources(config, ReplayHTTPClient(ROOT / "nonexistent"), "1")
    assert type(alert_source).__name__ == "SentryAlertSource"
    assert adapters == []


def test_adoption_guide_exists_and_is_linked() -> None:
    guide = ROOT / "docs" / "adoption.md"
    assert guide.exists()
    assert "sources.minimal.toml" in guide.read_text()
    assert "adoption.md" in (ROOT / "README.md").read_text()
