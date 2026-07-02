"""Read-only collection layer (v2): adapters in, incident packages out."""

from ai_incident_investigator.collect.adapter import (
    AlertBundle,
    AlertSource,
    CollectionContext,
    PackageContribution,
    SourceAdapter,
)
from ai_incident_investigator.collect.config import (
    CollectError,
    CollectionSettings,
    SourcesConfig,
    load_sources_config,
)
from ai_incident_investigator.collect.http import (
    EnvBearerAuth,
    HTTPClient,
    HTTPClientError,
    HTTPReplayMissError,
    HTTPRequest,
    HTTPResponse,
    LiveHTTPClient,
    RecordingHTTPClient,
    ReplayHTTPClient,
    raise_for_status,
)
from ai_incident_investigator.collect.local import LocalTopologyAdapter
from ai_incident_investigator.collect.orchestrator import (
    CollectionReport,
    SourceStatus,
    collect_package,
)

__all__ = [
    "AlertBundle",
    "AlertSource",
    "CollectError",
    "CollectionContext",
    "CollectionReport",
    "CollectionSettings",
    "EnvBearerAuth",
    "HTTPClient",
    "HTTPClientError",
    "HTTPReplayMissError",
    "HTTPRequest",
    "HTTPResponse",
    "LiveHTTPClient",
    "LocalTopologyAdapter",
    "PackageContribution",
    "RecordingHTTPClient",
    "ReplayHTTPClient",
    "SourceAdapter",
    "SourceStatus",
    "SourcesConfig",
    "collect_package",
    "load_sources_config",
    "raise_for_status",
]
