# Claude Code instructions

Read `AGENTS.md` first; it is the source of truth for commands, workflow, and rules in this
repo. `docs/product.md` is the product source of truth.

Claude-specific notes:

- Project skills live in `.claude/skills/`: `github-planning` (structuring milestones,
  epics, issues), `github-shipping` (PR delivery and pre-merge checks), `repo-review`
  (findings-first code review). Apply them when the task matches.
- Run `uv run ruff format .`, `uv run ruff check .`, `uv run mypy`, and `uv run pytest`
  before declaring any change done.
