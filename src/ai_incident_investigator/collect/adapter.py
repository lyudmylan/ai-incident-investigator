"""The source-adapter contract.

An adapter turns one external source into typed parts of the v1 incident
package - the package contract is the interface between collection and
investigation (Principle 4 extended: collection produces validated facts).

Two roles:
- AlertSource: fetches the anchor. Required; without a usable alert there is
  no incident window and collection fails loudly.
- SourceAdapter: contributes optional package parts. Failures degrade - the
  package simply lacks that file and the v1 loader reports the gap.

PackageContribution mirrors the graph's StateUpdate: adapters return typed
parts, only the orchestrator merges them.
"""

from datetime import datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from ai_incident_investigator.models.package import (
    Alert,
    DeploysFile,
    LogRecord,
    MetricsFile,
    TopologyFile,
    TracesFile,
)


class CollectionContext(BaseModel):
    """What every adapter gets: the anchor and the documented spans around it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    anchor_time: datetime = Field(description="alert.triggered_at - anchors all spans")
    lookback: timedelta = Field(description="incident-window lookback (docs/assumptions.md)")
    change_lookback: timedelta = Field(
        description="wider span for deploys/config changes; old changes rule things out"
    )
    services: list[str] = Field(default_factory=list)


class AlertBundle(BaseModel):
    """An alert source may carry event data that legitimately maps to logs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alert: Alert
    logs: list[LogRecord] = Field(default_factory=list)


class PackageContribution(BaseModel):
    """Typed parts one adapter contributes. Lists merge; single files must
    come from exactly one adapter (two metrics sources is a config bug)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metrics: MetricsFile | None = None
    logs: list[LogRecord] = Field(default_factory=list)
    traces: TracesFile | None = None
    deploys: DeploysFile | None = None
    topology: TopologyFile | None = None
    runbook: str | None = None
    notes: list[str] = Field(
        default_factory=list,
        description="honest caveats for the collection report: what was skipped "
        "or degraded inside an otherwise successful adapter run",
    )


class AlertSource(Protocol):
    @property
    def name(self) -> str: ...

    def fetch_alert(self) -> AlertBundle: ...


class SourceAdapter(Protocol):
    @property
    def name(self) -> str: ...

    def collect(self, context: CollectionContext) -> PackageContribution: ...
