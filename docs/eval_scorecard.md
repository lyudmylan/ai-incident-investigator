# Evaluation scorecard

Deterministic rubric results over the replayed corpus. Regenerate with
`uv run --no-sync python scripts/eval_corpus.py --write` after
intentional changes; tests/test_eval_corpus.py gates drift and failures.

**63/63 checks passing.**

## cascade_victim_alert
- PASS: top hypothesis names the culprit two hops away (session-store)
- PASS: three aligned sources earn HIGH confidence
- PASS: earliest metric deviation in the timeline is the session-store
- PASS: a runbook-grounded plan exists and links a real mitigation
- PASS: status page is identified-phase and customer-safe
- PASS: no blocked safety checks
- PASS: reasoning trace present

## collected_demo
- PASS: the collected package investigates like a hand-authored one
- PASS: no blocked safety checks
- PASS: reasoning trace present

## conflicting_metrics
- PASS: single hypothesis capped at MEDIUM by the conflict
- PASS: the conflicting control series is cited, not hidden
- PASS: internal update names the conflict
- PASS: no blocked safety checks
- PASS: reasoning trace present

## dependency_timeout
- PASS: ongoing incident watches for recovery
- PASS: top hypothesis targets the third-party dependency
- PASS: no blocked safety checks
- PASS: reasoning trace present

## error_rate_spike
- PASS: in-window recovery yields confirm-sustained mode
- PASS: justified severity downgrade holds (SEV-3 under a SEV-2 ceiling)
- PASS: no blocked safety checks
- PASS: reasoning trace present

## insufficient_evidence
- PASS: no hypothesis is forced from thin evidence
- PASS: no mitigations and no plans without a hypothesis
- PASS: the report still gives the human next steps (from the gaps)
- PASS: at least three distinct gaps are on the record
- PASS: severity is the honest floor (SEV-4, low confidence)
- PASS: no blocked safety checks
- PASS: reasoning trace present

## latency_spike
- PASS: SEV-2 with plans for the deploy-driven incident
- PASS: rollback checklist names the exact release
- PASS: no blocked safety checks
- PASS: reasoning trace present

## missing_baselines
- PASS: no hypothesis rises above LOW on single-source evidence
- PASS: no recovery plan is fabricated without metrics
- PASS: the metrics gap is reported
- PASS: no mitigation options on unquantified impact
- PASS: no blocked safety checks
- PASS: reasoning trace present

## operator_already_mitigated
- PASS: recovery mode is confirm-sustained (recovered in-window)
- PASS: operator action is described without executed-action phrasing
- PASS: severity downgrade below the ceiling is accepted
- PASS: status page is monitoring-phase and customer-safe
- PASS: no blocked safety checks
- PASS: reasoning trace present

## red_herring_deploy
- PASS: top hypothesis targets the disk, not the deploy
- PASS: deploy hypothesis is ranked last with LOW confidence
- PASS: deploy timing is marked misaligned (cause cannot postdate onset)
- PASS: severity stays at the numeric ceiling (SEV-2)
- PASS: no blocked safety checks
- PASS: reasoning trace present

## executor refusal matrix (v5 pilot)
- PASS: no approval at all is refused
- PASS: a tampered report (hash mismatch) is refused
- PASS: an expired approval is refused
- PASS: production quorum unmet (1/2) is refused
- PASS: the same identity approving twice still counts once (1/2, refused)
- PASS: two DISTINCT approvers meet production quorum (control: previewed)
- PASS: an unlisted flag is refused
- PASS: an unknown environment is refused
- PASS: production live is refused even at full quorum (pilot tier rule)
- PASS: strict separation of duties: the invoker's own approval never suffices
- PASS: staging dry-run at quorum is previewed (control)
