"""Generate contract docs from the Pydantic models.

Usage: uv run python -m ai_incident_investigator.contracts

Writes docs/incident_package_contract.md and docs/output_contract.md.
A test asserts the committed docs match the generated output, so contract
changes fail CI until the docs are regenerated in the same PR.
"""

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from ai_incident_investigator.models.package import IncidentPackage
from ai_incident_investigator.models.report import InvestigationReport

GENERATED_HEADER = (
    "<!-- GENERATED FILE - do not edit by hand."
    " Regenerate with: uv run python -m ai_incident_investigator.contracts -->\n"
)

PACKAGE_INTRO = """# Incident Package Contract

An incident package is a directory of files describing one incident.

| File | Required | Contents |
| --- | --- | --- |
| `alert.json` | yes | the alert that opened the incident; anchors the incident window |
| `metrics.json` | no | metric series with required pre-incident baselines |
| `logs.jsonl` | no | structured log records, one JSON object per line (preferred) |
| `logs.txt` | no | unstructured logs, parsed best-effort into the same record shape |
| `traces.json` | no | distributed trace spans |
| `deploys.json` | no | recent deploys, config changes, feature flag flips |
| `topology.json` | no | service dependency graph |
| `runbook.md` | no | free-form operational guidance, carried verbatim |

Missing optional files become `missing_data` entries in the report; they never
fail the run. All timestamps must be timezone-aware (UTC recommended). Unknown
fields are rejected.

The JSON Schema below describes the fully loaded package; the definitions for
each file's payload are under `$defs`.
"""

OUTPUT_INTRO = """# Output Contract

The investigation report is JSON-first and stable. Safety properties are part
of the schema itself: every mitigation option carries a constant
`requires_human_approval: true`, hypotheses cite evidence by id, and each
confidence label carries the rubric inputs that justify it
(see docs/assumptions.md).
"""


def render(intro: str, model: type[BaseModel]) -> str:
    schema = json.dumps(model.model_json_schema(), indent=2)
    return (
        f"{GENERATED_HEADER}{intro}\n## JSON Schema: `{model.__name__}`\n\n```json\n{schema}\n```\n"
    )


def contract_files(docs_dir: Path) -> dict[Path, str]:
    return {
        docs_dir / "incident_package_contract.md": render(PACKAGE_INTRO, IncidentPackage),
        docs_dir / "output_contract.md": render(OUTPUT_INTRO, InvestigationReport),
    }


def main() -> int:
    docs_dir = Path(__file__).resolve().parents[2] / "docs"
    for path, content in contract_files(docs_dir).items():
        path.write_text(content)
        print(f"wrote {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
