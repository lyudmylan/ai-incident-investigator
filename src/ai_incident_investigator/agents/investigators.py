"""The five source investigators (metrics, logs, traces, deploys, runbook).

Each owns one evidence source. Cross-source synthesis and hypothesis ranking
belong to epic #6; these agents only report what their own source shows,
with timestamps, so the ranker can correlate.
"""

from ai_incident_investigator.agents.base import InvestigatorSpec
from ai_incident_investigator.agents.rendering import (
    render_alert,
    render_deploys,
    render_logs,
    render_metrics,
    render_runbook,
    render_timeline,
    render_topology,
    render_traces,
    render_window,
)
from ai_incident_investigator.models.common import Source
from ai_incident_investigator.state import InvestigationState

METRICS_PROMPT = """\
Role: metrics investigator.
Input: metric series with pre-incident baselines, plus the incident window.
The documented deviation rule (docs/assumptions.md): a value is anomalous at
>= 2x baseline or <= 0.5x baseline; with a zero baseline any nonzero value
is anomalous.

Report as findings, each tied to a concrete series and timestamp:
- for each series that deviates: when it first crossed, how far it peaked
  relative to baseline, and whether it recovered inside the window
- ordering between deviating series (which moved first)
- series that stayed at baseline - unaffected services are evidence too
- suspicious shapes the rule misses (steady climb, step change, plateau)
Do not speculate about causes in other sources; describe metric behavior."""

LOGS_PROMPT = """\
Role: logs investigator.
Input: structured log records (timestamp, level, service, message) and the
incident window.

Report as findings, each citing concrete records via timestamp and service:
- error and warning patterns: what fails, where, starting when
- repetition and escalation (e.g. retries per operation, attempt counts,
  whether retry behavior is bounded)
- resource saturation messages (pools, queues, limits) with their numbers
- the first log record that departs from routine, and what changed at that
  moment
- notable silences: services logging normally through the window
Quote key message fragments verbatim inside interpretations."""

TRACES_PROMPT = """\
Role: trace investigator.
Input: distributed trace spans (grouped by trace) and the incident window.

Report as findings, each citing trace ids, spans, and durations:
- where time is spent in slow requests: which span dominates the critical
  path, and what fraction of the root duration it accounts for
- error propagation: which spans fail, and whether failures originate deep
  and surface upward
- before/after contrast when both healthy and degraded traces exist
  (same operations, changed durations)
- operations that stay fast even in degraded traces - they narrow the fault
Use duration numbers from the input; never estimate."""

DEPLOYS_PROMPT = """\
Role: deploy correlation investigator.
Input: deploys/config/flag changes, the alert, the incident window, and the
service topology.

Report as findings with exact timestamps:
- for each change: how long before/after symptom onset it landed, and
  whether that gap is consistent with causing the incident
- whether the changed service can reach the affected service through the
  topology (name the path), or cannot
- changes that are poor candidates (wrong service, wrong time, no path) -
  ruling out is evidence
Timing correlation is not causation: state alignment strength, not verdicts."""

RUNBOOK_PROMPT = """\
Role: runbook investigator.
Input: the service runbook verbatim, the alert, and the deterministic
timeline of observed events.

Report as findings:
- known failure modes from the runbook whose documented symptoms match
  observed timeline events (quote both sides)
- runbook checks relevant to what is being observed
- operational constraints and warnings the runbook states (e.g. when an
  action is documented as unsafe)
- mismatches: observed behavior no runbook failure mode covers
The runbook is guidance written in advance, not evidence of current state;
frame findings as "the runbook documents X, which matches observed Y"."""


def _metrics_input(state: InvestigationState) -> str:
    return "\n\n".join([render_window(state.window), render_metrics(state.package)])


def _logs_input(state: InvestigationState) -> str:
    return "\n\n".join([render_window(state.window), render_logs(state.package)])


def _traces_input(state: InvestigationState) -> str:
    return "\n\n".join([render_window(state.window), render_traces(state.package)])


def _deploys_input(state: InvestigationState) -> str:
    return "\n\n".join(
        [
            render_window(state.window),
            render_alert(state.package),
            render_deploys(state.package),
            render_topology(state.package),
        ]
    )


def _runbook_input(state: InvestigationState) -> str:
    return "\n\n".join(
        [
            render_window(state.window),
            render_alert(state.package),
            render_timeline(state.timeline),
            render_runbook(state.package),
        ]
    )


SOURCE_SPECS = [
    InvestigatorSpec(
        name="metrics_investigator",
        source=Source.METRICS,
        role_prompt=METRICS_PROMPT,
        render_input=_metrics_input,
        is_available=lambda state: state.package.metrics is not None,
    ),
    InvestigatorSpec(
        name="logs_investigator",
        source=Source.LOGS,
        role_prompt=LOGS_PROMPT,
        render_input=_logs_input,
        is_available=lambda state: len(state.package.logs) > 0,
    ),
    InvestigatorSpec(
        name="trace_investigator",
        source=Source.TRACES,
        role_prompt=TRACES_PROMPT,
        render_input=_traces_input,
        is_available=lambda state: state.package.traces is not None,
    ),
    InvestigatorSpec(
        name="deploy_correlation",
        source=Source.DEPLOYS,
        role_prompt=DEPLOYS_PROMPT,
        render_input=_deploys_input,
        is_available=lambda state: state.package.deploys is not None,
    ),
    InvestigatorSpec(
        name="runbook_investigator",
        source=Source.RUNBOOK,
        role_prompt=RUNBOOK_PROMPT,
        render_input=_runbook_input,
        is_available=lambda state: bool(state.package.runbook),
    ),
]
