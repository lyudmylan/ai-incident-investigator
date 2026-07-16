"""Deterministic config discovery (#80): generate the heavy per-service
sections of sources.toml from what live Prometheus/Loki actually contain.

Read-only GETs against the standard discovery APIs (`/api/v1/series`,
`/api/v1/label/{name}/values`, `/loki/api/v1/label/{name}/values`). The
tool PROPOSES and the human trims - no LLM anywhere, and the draft is
annotated so nothing reads as authoritative. Discovery requests carry no
time parameters on purpose: "what exists" is the right scope, the servers
default sensibly, and parameter-free requests replay byte-for-byte
(keyless demos, stable fixtures).

The generated entries are exactly the shapes collection consumes, so
`collect doctor` validates a generated draft unchanged.
"""

from pydantic import BaseModel, ConfigDict, Field

from ai_incident_investigator.collect.http import (
    EnvBearerAuth,
    HTTPClient,
    HTTPRequest,
    raise_for_status,
)

MAX_METRICS_PER_SERVICE = 20
MAX_DISCOVERED_SERVICES = 10


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class LabelValuesResponse(_WireModel):
    """`/api/v1/label/{name}/values` (Prometheus) and
    `/loki/api/v1/label/{name}/values` (Loki) share this shape."""

    status: str
    data: list[str] = Field(default_factory=list)


class SeriesListResponse(_WireModel):
    """Prometheus `/api/v1/series`: a list of label sets."""

    status: str
    data: list[dict[str, str]] = Field(default_factory=list)


def _get_json(http: HTTPClient, request: HTTPRequest, auth: EnvBearerAuth | None) -> str:
    return raise_for_status(request, http.get(request, auth)).body


def _label_values(http: HTTPClient, url: str, auth: EnvBearerAuth | None) -> list[str]:
    parsed = LabelValuesResponse.model_validate_json(_get_json(http, HTTPRequest(url=url), auth))
    if parsed.status != "success":
        raise RuntimeError(f"label values returned status={parsed.status!r}")
    return sorted(parsed.data)


def discover_prometheus(
    http: HTTPClient,
    base_url: str,
    service_label: str,
    services: list[str],
    auth: EnvBearerAuth | None = None,
) -> tuple[dict[str, list[str]], list[str]]:
    """Per service: the metric names Prometheus actually has for it.

    Returns (service -> sorted metric names, notes). Empty results carry a
    hint instead of silence - the usual cause is the wrong service label.
    """
    base = base_url.rstrip("/")
    notes: list[str] = []
    targets = list(services)
    if not targets:
        values = _label_values(http, f"{base}/api/v1/label/{service_label}/values", auth)
        if not values:
            notes.append(
                f'prometheus: label "{service_label}" has no values - try '
                "--service-label job (or your platform's service label)"
            )
        if len(values) > MAX_DISCOVERED_SERVICES:
            notes.append(
                f"prometheus: {len(values)} services discovered; keeping the first "
                f"{MAX_DISCOVERED_SERVICES} - pass --service to choose explicitly"
            )
            values = values[:MAX_DISCOVERED_SERVICES]
        targets = values
    metrics: dict[str, list[str]] = {}
    for service in targets:
        request = HTTPRequest(
            url=f"{base}/api/v1/series",
            params={"match[]": f'{{{service_label}="{service}"}}'},
        )
        parsed = SeriesListResponse.model_validate_json(_get_json(http, request, auth))
        if parsed.status != "success":
            notes.append(f"prometheus {service}: series returned status={parsed.status!r}")
            continue
        names = sorted({entry.get("__name__", "") for entry in parsed.data} - {""})
        if not names:
            notes.append(
                f'prometheus {service}: no series with {service_label}="{service}" - '
                "wrong label? try --service-label job"
            )
            continue
        if len(names) > MAX_METRICS_PER_SERVICE:
            notes.append(
                f"prometheus {service}: {len(names)} metrics found; keeping the first "
                f"{MAX_METRICS_PER_SERVICE} - trim the draft to the signals that matter"
            )
            names = names[:MAX_METRICS_PER_SERVICE]
        metrics[service] = names
    return metrics, notes


def discover_loki(
    http: HTTPClient,
    base_url: str,
    label: str,
    services: list[str],
    auth: EnvBearerAuth | None = None,
) -> tuple[list[str], list[str]]:
    """The values of the Loki stream label worth proposing selectors for."""
    base = base_url.rstrip("/")
    notes: list[str] = []
    values = _label_values(http, f"{base}/loki/api/v1/label/{label}/values", auth)
    if not values:
        notes.append(
            f'loki: label "{label}" has no values - try --loki-label service_name '
            "(or check Loki's label browser)"
        )
        return [], notes
    if services:
        missing = sorted(set(services) - set(values))
        for service in missing:
            notes.append(f'loki: no streams with {label}="{service}" - selector not proposed')
        return [value for value in values if value in services], notes
    if len(values) > MAX_DISCOVERED_SERVICES:
        notes.append(
            f"loki: {len(values)} label values; keeping the first "
            f"{MAX_DISCOVERED_SERVICES} - pass --service to choose explicitly"
        )
        values = values[:MAX_DISCOVERED_SERVICES]
    return values, notes


def render_draft(
    prometheus_url: str | None,
    prometheus_token_env: str | None,
    service_label: str,
    metrics: dict[str, list[str]],
    loki_url: str | None,
    loki_token_env: str | None,
    loki_label: str,
    loki_services: list[str],
) -> str:
    """The draft sources.toml: discovered sections filled in and annotated,
    human-knowledge sections as commented TODO blocks."""
    services = sorted(set(metrics) | set(loki_services)) or ["your-service"]
    lines = [
        "# DRAFT sources.toml - generated by `init --discover`; TRIM before use.",
        "# Discovered entries reflect what the live systems contained at",
        "# generation time; keep the signals that matter for incidents and",
        "# delete the rest. TODO sections need human knowledge.",
        "# Validate any edit with:  collect doctor --sources <this file>",
        "",
        "[collection]",
        f"services = {services!r}".replace("'", '"'),
        "lookback_minutes = 30",
        "change_lookback_days = 7",
        "",
        "# TODO: the alert anchor is the one REQUIRED source (docs/adoption.md)",
        "[sentry]",
        'base_url = "https://sentry.example.com/api/0" # TODO: your install',
        'token_env = "SENTRY_TOKEN" # read-only; value goes in .env',
        "",
    ]
    if prometheus_url is not None:
        lines += ["[prometheus]", f'base_url = "{prometheus_url}"']
        if prometheus_token_env:
            lines.append(f'token_env = "{prometheus_token_env}"')
        lines.append("")
        for service, names in sorted(metrics.items()):
            for name in names:
                lines += [
                    "[[prometheus.queries]] # discovered",
                    f'service = "{service}"',
                    f'signal = "{name}"',
                    f"query = '{name}{{{service_label}=\"{service}\"}}'",
                    '# unit = "ms" # TODO if known',
                    "",
                ]
    if loki_url is not None:
        lines += ["[loki]", f'base_url = "{loki_url}"']
        if loki_token_env:
            lines.append(f'token_env = "{loki_token_env}"')
        lines.append("")
        for service in loki_services:
            lines += [
                "[[loki.streams]] # discovered",
                f'service = "{service}"',
                f"selector = '{{{loki_label}=\"{service}\"}}'",
                "",
            ]
    lines += [
        "# TODO: uncomment and fill in (docs/adoption.md steps 4-5)",
        "# [github]",
        '# base_url = "https://api.github.com"',
        '# token_env = "GITHUB_READ_TOKEN"',
        "# [[github.repos]]",
        '# repo = "you/your-service"',
        f'# service = "{services[0]}"',
        "#",
        "# [runbook]",
        "# [[runbook.documents]]",
        '# file = "runbooks/your-service.md"',
        "#",
        "# [topology]",
        '# file = "topology.json"',
        "",
    ]
    return "\n".join(lines)
