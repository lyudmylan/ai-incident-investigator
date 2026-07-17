# Learning design (v7 pilot)

Epic #86: "learn from incident patterns", the last roadmap item, deferred
in v5 until execution earned trust. The pilot is a **deterministic,
explainable memory over the tool's own past investigations**: when a new
incident resembles a past one, the report says so, says exactly why, and
says what was actually executed last time and whether that fix VERIFIED.
Precedent for the human - never a shortcut for the tool.

Contracts: `models/history.py` (generated schema in
docs/learning_contract.md). Logic: `patterns.py`, pure functions only.
The normative matching rule: docs/assumptions.md ("Pattern matching
rule").

## What the pilot learns from

The tool's own artifacts, nothing else:

- the investigation report (JSON), which already carries structured,
  comparable facts: affected services, evidence-cited (service, signal)
  pairs, severity, hypothesis-to-evidence links, recovery baselines;
- the executions sidecar (v5), which records what was actually attempted
  and how its verification ended.

No network, no LLM, no new credentials, no telemetry access. History is a
local directory the user controls; entries are content-addressed copies of
reports the tool itself produced.

## The fingerprint

A pure function of the report file (plus the executions sidecar when one
exists) - the same bytes in always produce the same fingerprint out.
Wall-clock time and free text are never inputs: hypothesis titles,
interpretations, and drafts are prose for humans; the fingerprint uses
only structured fields, so two reports written in different words about
the same behavior still compare truthfully.

Deviation direction is derived only when the report's own numbers allow
it: the median of metrics-sourced evidence values for a (service, signal)
pair, compared against the recovery plan's baseline for that exact pair.
Anything less grounded is `"unknown"` - direction is never inferred from
wording like "spiked" or "dropped".

## Matching: a gate, then explainable arithmetic

The gate: **no shared abnormal (service, signal) pair, no match.** Two
incidents that merely share a severity level or a deploy correlation have
nothing behaviorally in common; the rule refuses to call them similar.

Past the gate, the score is a sum of documented feature weights, and the
match record carries every matched feature WITH its weight - the score is
recomputable from the record (the same auditability rule the confidence
rubric follows). Every difference the rule inspects lands in `unmatched`,
so a match can never present similarity without also presenting how the
incidents differ.

## What a match asserts - and refuses to assert

A match asserts **resemblance of observed behavior**. It never asserts
"same root cause" - two look-alike incidents can have different causes,
and pretending otherwise is exactly the overclaim this product exists to
prevent. Deliberate consequences:

- **Priors never move conclusions.** Severity, hypotheses, confidence
  labels, rankings, and every agent output are byte-identical with and
  without history. Invariance is a test (and, from v7 hardening on, a
  scored eval row), not a convention.
- **Only verified outcomes are precedent.** "This fix verified-recovered
  a similar incident" requires a verification record with a `verified`
  verdict. An applied-but-unverified or failed fix surfaces as a caution
  ("tried there; did NOT verify") - the schema cannot even represent a
  previewed or refused execution as a tried fix.
- **A re-investigation is labeled.** An earlier report for the same
  incident id may match, but the record flags it: an incident cannot
  serve as its own independent precedent.
- **Nothing auto-anything.** A verified precedent does not pre-approve,
  pre-rank, or pre-execute anything. The approval quorum, the allowlist,
  and every v5 gate are untouched.

## Why zero LLM involvement

Three reasons, in order:

1. **Trust.** The learning layer's whole value is that a human can check
   its work by reading the record. A similarity model cannot be audited
   line by line; feature equality can.
2. **Economics.** Prompts are untouched, so every committed fixture and
   live recording keeps replaying byte-identically, and the feature costs
   zero tokens forever.
3. **Scope honesty.** Feeding history into agent prompts would hide the
   learning inside the model where it can neither be tested nor bounded.
   If richer similarity ever earns its place, it arrives as a new,
   separately-evaluated stage - not as prompt seasoning.

## Honest limitations

- History quality is the user's responsibility: the store trusts that its
  entries are reports this tool produced. Contract validation rejects
  malformed entries, but a curated-garbage history yields garbage
  precedent (deterministically, at least).
- Structured-feature equality is deliberately narrow. Renamed services or
  signals break matching; synonyms are invisible. The pilot prefers
  false negatives to plausible-but-wrong precedent.
- Fingerprints compare what reports CITE, which reflects what was
  collected. Two identical outages observed through different telemetry
  configurations may not match.
