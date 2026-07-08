Product
Product Name
AI Incident Investigator
One-Liner
AI Incident Investigator is an explainable, human-in-the-loop investigation layer for a future AI SRE agent.
Goal
Given an offline incident package with alerts, logs, metrics, traces, deploy history, topology, and runbooks, investigate the incident, correlate evidence, rank likely hypotheses, and recommend safe next actions.
The first product goal is not autonomous remediation. The first goal is explainable incident investigation.
Product Framing
Production incidents are rarely solved by one magic answer.
They require:
	•	collecting signals from multiple systems
	•	separating symptoms from possible causes
	•	correlating telemetry with recent changes
	•	checking runbooks and known failure modes
	•	ranking hypotheses by evidence
	•	communicating clearly under pressure
	•	choosing safe actions with human approval
This project explores how agent orchestration can support that workflow.
AI Incident Investigator is intentionally scoped as the investigation layer of a broader future AI SRE agent.
Future Direction
The long-term direction is an AI SRE agent that can help with the full incident lifecycle:
	1	detect
	2	triage
	3	investigate
	4	recommend
	5	execute approved actions
	6	verify recovery
	7	generate postmortems
	8	learn from previous incidents
This repo starts with steps 2-4 and 7.
Execution of production actions is explicitly out of scope for the first version.
Current Shape
The initial product should be an offline, JSON-first CLI.
It should accept an incident directory as input and produce structured JSON output plus optional human-readable summaries.
Example:
python -m ai_incident_investigator --incident examples/incidents/latency_spike
The incident package may include:
	•	alert.json
	•	metrics.json
	•	logs.jsonl (preferred, structured) or logs.txt (best-effort fallback)
	•	traces.json
	•	deploys.json
	•	topology.json
	•	runbook.md

metrics.json must include pre-incident baseline values for each signal, otherwise "abnormal" cannot be determined offline.
The incident window is determined by an explicit, documented rule: alert trigger time minus a configurable lookback, overridable via CLI flag. The rule lives in docs/assumptions.md.
The output should include:
	•	incident summary
	•	severity assessment
	•	timeline
	•	evidence items
	•	ranked hypotheses
	•	missing data
	•	recommended next investigation steps
	•	safe mitigation options
	•	safety review
	•	internal communication draft
	•	postmortem draft
	•	reasoning trace (machine-readable record of why each stage concluded what it did)

Top-level recommended next steps are an aggregation that references the hypotheses and missing-data entries they come from, so the output stays internally consistent.
Product Principles
1. Investigation first, remediation later
The product should not execute rollback, restart services, change infrastructure, modify feature flags, or page people.
It may recommend actions, but execution must remain human-approved and outside the tool.
2. Evidence-backed reasoning
Every hypothesis should include supporting evidence.
Bad output:
The database is probably overloaded.
Good output:
Hypothesis: database saturation contributed to booking latency.

Evidence:
- database CPU reached 92 percent during the incident window
- p95 booking latency increased at the same time
- traces show slow calls from booking-service to appointments-db
- no matching spike appears in unrelated services
3. Do not hide uncertainty
The system should explicitly show:
	•	confidence
	•	assumptions
	•	missing data
	•	conflicting evidence
	•	next checks needed
4. Deterministic facts, agentic reasoning
Parsing, validation, timeline construction, and schema validation should be deterministic.
The agentic layer should handle:
	•	investigation planning
	•	evidence correlation
	•	hypothesis generation
	•	critique
	•	communication
	•	postmortem drafting
5. Human-in-the-loop by design
The product should require human approval before any remediation-like recommendation is treated as actionable.
The system should say:
Recommended for human approval:
- disable feature flag payment_enrichment
- rollback release 2026.06.01-1420
It should not say:
Rollback has been executed.
6. Small teams first
The first target user is a small-to-mid SaaS engineering team without a large dedicated SRE organization.
The product should feel useful for:
	•	VP R&D
	•	CTO
	•	engineering manager
	•	tech lead
	•	on-call engineer
	•	DevOps / platform engineer
Target Use Case
A production incident occurs in a SaaS product.
The team has several observability signals, but they are scattered:
	•	alert from monitoring
	•	application logs
	•	metrics snapshot
	•	traces
	•	deploy history
	•	topology
	•	runbook
The tool helps the team quickly answer:
	•	what happened?
	•	when did it start?
	•	what changed recently?
	•	which services are affected?
	•	what evidence supports each hypothesis?
	•	what should we check next?
	•	what safe actions can we consider?
	•	what should we communicate internally?
	•	what should go into the postmortem?
Example Scenario
A telemedicine platform has increased latency in appointment booking.
Symptoms:
	•	p95 latency increased from 450ms to 3200ms
	•	booking error rate increased from 0.3 percent to 4.8 percent
	•	database CPU is high
	•	queue depth increased
	•	recent deploy changed payment eligibility logic
	•	patients may be unable to complete appointment booking
The tool should investigate the package and produce an evidence-backed recommendation.
Example output direction:
Severity: SEV-2

Most likely hypothesis:
Recent payment eligibility deploy increased database pressure in the booking flow.

Supporting evidence:
- latency spike started 11 minutes after deploy
- traces show slow calls from booking-service to payment-service
- database CPU increased during the same window
- booking-service logs show repeated eligibility retry warnings

Recommended next checks:
- compare error rate before and after release 2026.06.01-1420
- inspect payment eligibility retry behavior
- check whether retries are bounded
- verify whether feature flag payment_enrichment can be disabled safely

Safe mitigation options:
- disable payment enrichment feature flag
- rollback latest booking-service release
- temporarily increase worker concurrency only if queue pressure remains high

Human approval required before mitigation.
Initial Product Scope - v1
v1 should support offline investigation packages and be agentic from the start.
Deterministic facts, agentic reasoning (Principle 4) applies directly: parsing, validation, and timeline construction are deterministic code; investigation, hypothesis ranking, critique, and drafting are LLM-backed agents.
In Scope
	•	CLI entry point
	•	incident package loading
	•	required and optional file validation
	•	deterministic timeline construction
	•	graph-based agent orchestration
	•	specialized LLM-backed investigator agents (parallel where useful)
	•	severity assessment
	•	evidence extraction from structured and text inputs
	•	hypothesis ranking with structured confidence
	•	safety critic loop
	•	missing data reporting
	•	safe next-step recommendation
	•	internal update draft
	•	postmortem draft
	•	reasoning trace for explainability
	•	JSON output contract
	•	example incidents
	•	tests, linting, type checking, and CI
	•	LLM calls mocked or replayed in tests so CI runs without API keys
Out of Scope
	•	live production integrations
	•	automatic remediation
	•	rollback execution
	•	feature flag changes
	•	infrastructure changes
	•	paging or Slack posting
	•	direct Datadog / Sentry / Grafana / Prometheus integration
	•	long-term memory
	•	user authentication
	•	web UI
	•	database
	•	autonomous incident ownership
v1 Workflow
Incident Package
  ↓
Package Loader
  ↓
Validation
  ↓
Timeline Builder
  ↓
Parallel Investigation
  - Alert analysis
  - Metrics analysis
  - Logs analysis
  - Trace analysis
  - Deploy correlation
  - Runbook lookup
  ↓
Evidence Builder
  ↓
Hypothesis Ranker
  ↓
Safety Critic
  ↓
Recommendation Builder
  ↓
Comms + Postmortem Draft
  ↓
JSON Output
Agentic Architecture Direction
v1 is agentic from the start: a deterministic loading/validation/timeline layer feeds a graph-based orchestration of LLM-backed agents.
Agents receive pre-validated, structured facts — never raw unparsed files — so their reasoning stays auditable.
Recommended agent roles:
Triage Agent
Determines severity, affected service, likely blast radius, and urgency.
Metrics Investigator
Analyzes metrics snapshots and identifies abnormal behavior.
Logs Investigator
Finds relevant log patterns, errors, warnings, and timestamp correlations.
Trace Investigator
Identifies slow spans, failing dependencies, and request-path degradation.
Deploy Correlation Agent
Checks whether incident timing aligns with deploys, releases, or config changes.
Runbook Agent
Retrieves relevant operational guidance and known failure modes.
Hypothesis Ranker
Combines findings into ranked hypotheses with evidence and confidence.
Safety Critic
Challenges unsafe recommendations, missing evidence, overconfident claims, and risky actions.
Communication Agent
Drafts internal updates, customer-safe summaries, and postmortem sections.
Output Contract Direction
The output should remain stable and JSON-first.
Top-level output fields should include:
{
  "incident_id": "incident_001",
  "summary": {},
  "severity": {},
  "timeline": [],
  "evidence": [],
  "hypotheses": [],
  "missing_data": [],
  "recommended_next_steps": [],
  "safe_mitigation_options": [],
  "safety_review": {},
  "communication_drafts": {},
  "postmortem_draft": {},
  "reasoning_trace": []
}
Each hypothesis should include:
{
  "id": "hypothesis_001",
  "title": "Recent deploy caused booking latency",
  "confidence": "high",
  "supporting_evidence_ids": [],
  "conflicting_evidence_ids": [],
  "assumptions": [],
  "recommended_checks": []
}
Each evidence item should include:
{
  "id": "evidence_001",
  "source": "metrics",
  "timestamp": "2026-06-01T09:15:00Z",
  "service": "booking-service",
  "signal": "p95_latency_ms",
  "value": 3200,
  "interpretation": "p95 latency increased significantly during the incident window"
}
Safety Model
The tool may recommend:
	•	investigation steps
	•	rollback consideration
	•	feature flag disablement consideration
	•	scaling consideration
	•	runbook section to follow
	•	owner assignment suggestion
	•	communication drafts
The tool must not execute:
	•	rollback
	•	deployment
	•	restart
	•	scaling
	•	config changes
	•	feature flag updates
	•	database migration
	•	destructive commands
	•	customer communication
All remediation-like output must be framed as requiring human approval.

The single write exception (v4): the tool may publish its OWN
investigation report to the team's issue tracker. It still never executes
remediation, never posts customer communications, never pages. The write
is narrowed structurally: the publish client can represent exactly one
operation (create issue) against a repo name, with its own credential,
isolated from all collection configuration.
Severity Model
v1 should support a simple severity classification:
	•	SEV-1: critical production outage, major customer or patient impact, no workaround
	•	SEV-2: significant degradation, partial outage, important customer impact
	•	SEV-3: limited degradation, workaround exists, small customer impact
	•	SEV-4: low impact, warning, investigation needed but no active incident
Severity should include explanation and confidence.
Confidence Model
Use explicit confidence labels:
	•	high
	•	medium
	•	low
Confidence should be based on evidence quality, not wording strength.
High confidence requires multiple aligned signals.
Low confidence should be used when:
	•	data is missing
	•	signals conflict
	•	timing does not align
	•	evidence is indirect
	•	the hypothesis is plausible but not proven
Example Incident Types
Initial examples should include:
	1	Latency spike after deploy
	2	Error-rate increase in one service
	3	Dependency timeout
	4	Queue backlog
	5	Database saturation
	6	Memory leak after release
	7	Third-party API degradation
The first example should be latency_spike.
Roadmap
v1 - Agentic Offline Incident Investigator
	•	offline incident package
	•	deterministic package validation
	•	deterministic timeline construction
	•	graph-based orchestration of LLM-backed agents
	•	specialized investigator agents with parallel analysis
	•	evidence extraction
	•	hypothesis ranking with structured confidence model
	•	safety critic loop
	•	reasoning trace
	•	JSON output
	•	internal update and postmortem draft
	•	no live integrations
v2 - Read-Only Integrations
	•	Sentry-like issue input
	•	Prometheus-like metrics input
	•	log provider adapter
	•	GitHub deploy/release adapter
	•	runbook retrieval
	•	read-only mode only
v3 - Guided AI SRE Agent
	•	human-approved remediation plans
	•	Jira ticket draft
	•	Slack update draft
	•	status-page update draft
	•	rollback checklist
	•	recovery verification plan
v4 - Guided Operations (earn closed-loop before building it)
	•	adversarial evaluation corpus with a scored rubric (the trust ledger)
	•	publish the investigation report to the team's tracker (the first,
		narrowest write path - the tool's own analysis, nothing else)
	•	approval and audit records, exercised while still draft-only
	•	verify recovery deterministically: compare pre/post metrics from a
		second collected snapshot
v5 - Closed-Loop Assistance (pilot)
	•	execute ONE approved action type through a controlled adapter
		(feature-flag toggle: most reversible, best verification story),
		consuming v4 approval records; dry-run mandatory, allowlisted targets
	•	update postmortem from verified recovery
	•	learn from incident patterns (deferred until execution earns trust)
Execution remains explicitly gated and auditable.
Success Criteria
The project is successful when it can:
	•	load a realistic incident package
	•	construct a clear incident timeline
	•	identify relevant evidence across multiple sources
	•	rank plausible hypotheses
	•	avoid overclaiming root cause
	•	show missing data and assumptions
	•	recommend safe next steps
	•	produce useful communication drafts
	•	produce a useful postmortem draft
	•	keep remediation out of scope unless explicitly approved in future versions

Status (2026-07-08, end of v4): every criterion is met and demonstrable
offline at zero token cost - loading/timeline/evidence/hypotheses via the
replay demos (ten example packages including six adversarial ones, each
rubric-scored in CI); overclaim avoidance via the code-derived confidence
rubric, the severity ceiling lint, and the insufficient_evidence scenario
whose CORRECT scored answer is "no hypothesis"; missing data and
assumptions as first-class report fields; safe next steps, communication
drafts, and postmortem drafts in every golden; remediation still executes
nothing (approval records exist, the executor does not). Two live runs
(Haiku, Sonnet 5) validated the pipeline against the real API; committed
sample reports under docs/samples/.
Quality Bar
The project should follow engineering discipline from day one:
	•	JSON contracts before implementation
	•	example incident packages
	•	tests for validation and output shape
	•	linting
	•	type checking
	•	CI
	•	docs updated with behavior changes
	•	no hidden business logic in prompts
	•	no unsafe production actions
	•	no live credentials
	•	no private production data
Documentation Map
	•	docs/product.md
	◦	product goal, scope, principles, roadmap
	•	docs/architecture.md
	◦	system design, modules, workflow, agent graph
	•	docs/assumptions.md
	◦	severity rules, confidence rules, safety assumptions
	•	docs/incident_package_contract.md
	◦	input files and schemas
	•	docs/output_contract.md
	◦	output JSON structure
	•	AGENTS.md
	◦	repository instructions for coding agents
	•	CLAUDE.md
	◦	Claude Code working instructions