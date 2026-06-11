---
name: "repo-review"
description: "Use when the user asks for a code review, PR review, or an external-style review of changes in this repo. It focuses on bugs, regressions, missing tests, contract drift, and error-handling gaps."
---

# Repo Review

## When to use

- The user asks for a review of a change, PR, branch, or diff.
- The user wants an external-style code review in this repo.
- The work needs findings-first review output rather than implementation or shipping.

## How to use this skill

1. Read `AGENTS.md` first.
2. Review with a findings-first mindset.
3. Prioritize:
   - bugs
   - behavioral regressions
   - missing or weak tests
   - contract drift
   - error handling gaps
   - logging or observability regressions
   - hidden hardcoding of change-prone policy
4. Report findings first, ordered by severity, with concrete file references.
5. If there are no findings, say that explicitly and note any residual risks or testing gaps.

## Boundaries

- Use `github-shipping` when the user wants to ship or merge a change.
- Use this skill when the primary task is review, not delivery.
