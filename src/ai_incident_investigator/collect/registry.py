"""Assemble the collection source set from sources.toml.

The [sentry] section is required - it is v2's only alert source, and
collection has no anchor without it. Every other adapter joins when its
section is present; absence is not an error (the package will simply lack
that file, and the investigation reports the gap).
"""

from ai_incident_investigator.collect.adapter import AlertSource, SourceAdapter
from ai_incident_investigator.collect.config import CollectError, SourcesConfig
from ai_incident_investigator.collect.github import SECTION as GITHUB_SECTION
from ai_incident_investigator.collect.github import github_adapter
from ai_incident_investigator.collect.http import HTTPClient
from ai_incident_investigator.collect.local import LocalTopologyAdapter
from ai_incident_investigator.collect.loki import SECTION as LOKI_SECTION
from ai_incident_investigator.collect.loki import loki_adapter
from ai_incident_investigator.collect.prometheus import SECTION as PROMETHEUS_SECTION
from ai_incident_investigator.collect.prometheus import prometheus_adapter
from ai_incident_investigator.collect.runbook import SECTION as RUNBOOK_SECTION
from ai_incident_investigator.collect.runbook import runbook_adapter
from ai_incident_investigator.collect.sentry import SECTION as SENTRY_SECTION
from ai_incident_investigator.collect.sentry import sentry_alert_source

TOPOLOGY_SECTION = "topology"


def build_sources(
    config: SourcesConfig, http: HTTPClient, issue_id: str
) -> tuple[AlertSource, list[SourceAdapter]]:
    if not config.has_section(SENTRY_SECTION):
        raise CollectError(
            f"sources config {config.path} has no [{SENTRY_SECTION}] section; "
            "v2 collection needs it as the alert anchor source"
        )
    alert_source: AlertSource = sentry_alert_source(config, http, issue_id)

    adapters: list[SourceAdapter] = []
    if config.has_section(PROMETHEUS_SECTION):
        adapters.append(prometheus_adapter(config, http))
    if config.has_section(LOKI_SECTION):
        adapters.append(loki_adapter(config, http))
    if config.has_section(GITHUB_SECTION):
        adapters.append(github_adapter(config, http))
    if config.has_section(RUNBOOK_SECTION):
        adapters.append(runbook_adapter(config, http))
    if config.has_section(TOPOLOGY_SECTION):
        section = config.section(TOPOLOGY_SECTION)
        file = section.get("file")
        if not isinstance(file, str):
            raise CollectError(f'[{TOPOLOGY_SECTION}] section needs file = "path"')
        adapters.append(LocalTopologyAdapter(config.resolve_path(file)))
    return alert_source, adapters
