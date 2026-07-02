"""Built-in adapters for hand-authored local files.

Topology has no standard observability source (docs/product.md v2 note), so
it stays a hand-maintained file the orchestrator copies - validated - into
every collected package. This is also the smallest possible proof of the
adapter contract: no HTTP involved.
"""

import json
from pathlib import Path

from pydantic import ValidationError

from ai_incident_investigator.collect.adapter import CollectionContext, PackageContribution
from ai_incident_investigator.collect.config import CollectError
from ai_incident_investigator.models.package import TopologyFile


class LocalTopologyAdapter:
    def __init__(self, file: Path) -> None:
        self._file = file

    @property
    def name(self) -> str:
        return "topology"

    def collect(self, context: CollectionContext) -> PackageContribution:
        if not self._file.is_file():
            raise CollectError(f"topology file not found: {self._file}")
        try:
            topology = TopologyFile.model_validate(json.loads(self._file.read_text()))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise CollectError(f"topology file {self._file} is invalid: {exc}") from exc
        return PackageContribution(topology=topology)
