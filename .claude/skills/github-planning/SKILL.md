---
name: "github-planning"
description: "Use when the user wants to plan work in GitHub for this repo: create or update milestones, epics, issues, assignments, or roadmap structure based on docs/product.md and the repo's normal workflow."
---

# GitHub Planning

## When to use

- The user wants to create or organize GitHub milestones, epics, or issues.
- The user wants to break product direction into GitHub work items.
- The user wants to assign issues, group issues under a milestone, or update roadmap structure.
- The user is planning work, not shipping an already-implemented code change.

## How to use this skill

1. Read `AGENTS.md` first.
2. Use `docs/product.md` as the product source of truth.
3. Inspect existing milestones and issues before creating new ones.
4. Prefer a small, clear structure:
   - milestone first
   - epic issues next
   - implementation issues after that
5. Keep titles concise and stable.
6. In issue bodies, include:
   - goal
   - in scope
   - out of scope
   - notes or source-of-truth reference when useful
7. Assign issues only when the user asks or when the repo workflow clearly calls for it.
8. When implementation work is shipped in a PR, link the PR back to the relevant issues.
9. Prefer PR descriptions that use closing keywords such as `Closes #123` when the PR fully completes an issue.
10. If a PR should not close an issue yet, add an issue comment with the PR link and current status instead.

## Default workflow

For product planning:

1. Align the work structure to `docs/product.md`.
2. Create or update a milestone when the user is organizing a version or phase.
3. Create epic issues for major workstreams before creating implementation tasks.
4. Link the issue content back to the relevant product direction or functional design.
5. When a PR implements one or more issues, update the PR description or issue comments so traceability is visible from both sides.

For issue maintenance:

1. Check whether the target milestone or issue already exists.
2. Update existing issues when that is cleaner than creating duplicates.
3. Keep issue descriptions short and operational.

## What this skill adds

- a consistent GitHub planning workflow for this repo
- milestone-first planning when the user is organizing a release
- epic-first breakdown before task-level issue creation
- issue content grounded in `docs/product.md` instead of ad hoc wording
- PR-to-issue traceability so implementation status is visible in GitHub

## Boundaries

- Use `github-shipping` when the user wants to ship, push, merge, or complete code.
- Use this skill when the user wants to structure work in GitHub before or alongside implementation.
