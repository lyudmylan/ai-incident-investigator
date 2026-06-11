---
name: "github-shipping"
description: "Use when the user wants to finish, ship, push, merge, or complete a routine code change with the normal GitHub workflow. It applies the repo's shared shipping workflow and honors any stricter repo-specific AGENTS.md rules."
---

# GitHub Shipping

## When to use

- The user asks to ship, push, merge, finish, or complete a routine change.
- The work is already implemented and needs the standard GitHub delivery flow.
- The repo does not explicitly require a different release process.

## How to use this skill

1. Read the repo's `AGENTS.md` first.
2. Apply the repo's shared shipping workflow.
3. If the repo defines stricter or different rules, follow the repo-specific override.
4. Before merging a PR, verify all three review surfaces:
   - PR status/checks
   - top-level PR reviews and comments
   - inline review comments on the diff
5. After CI turns green, do one final review sweep across those same three surfaces.
6. Do not treat a PR as ready just because CI is green.
7. If there are actionable review findings, address them or explicitly decide to defer them before merge.
8. If `scripts/pr_ready_check.sh` exists, prefer `scripts/pr_ready_check.sh <pr-number>` for the final merge-readiness sweep.
9. Respect all required status checks; never merge around a failing required check unless the repo owner explicitly approves an override.

## What this skill adds

Use this skill as the trigger for shipping mode, especially when the user did not restate the whole workflow.

Default review focus:

- behavioral regressions
- missing or weak tests
- broken contracts or schema drift
- error handling gaps
- logging or observability regressions
- hidden hardcoding of change-prone policy

## Pre-Merge Checklist

For GitHub PRs in this repo, check:

1. PR status and checks
   - example: `gh pr view --json state,mergeStateStatus,reviewDecision,statusCheckRollup`
2. Top-level PR reviews and comments
   - example: `gh pr view --json reviews,comments`
3. Inline review comments
   - example: `gh api repos/<owner>/<repo>/pulls/<number>/comments`
4. Re-run the three review checks once after CI is green and immediately before merge
5. If `scripts/pr_ready_check.sh` exists, prefer it as the final single-command summary before merge
6. If a required check is failing, wait for it to pass or get the repo owner's explicit approval before merge

If any inline or review comments contain actionable findings, handle them before merge or call them out explicitly to the user.
