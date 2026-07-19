"""The local history store: past investigations as queryable precedent
(epic #86, issue #88).

The store IS the artifacts: one directory per entry under a user-chosen
history dir, holding a byte-verbatim copy of the report (so the entry's
sha256 stays true), copies of its sidecars when they exist, and the
derived entry.json. Anything index-like is derived by scanning - there is
no second source of truth to drift.

Write boundary: `add` writes ONLY inside the history directory, and only
into an entry directory that does not exist yet (entries are
content-addressed and immutable; re-adding the same report is a no-op).
`list` and `match` write nothing at all - matching against an APPROVED
report reads it and leaves it byte-identical, so approvals stay valid
(docs/assumptions.md, "Approval semantics").

Degradation rule: a corrupt or foreign entry is a note, never a crash -
one bad directory cannot take down add, list, or match.
"""

import shutil
from pathlib import Path

from pydantic import ValidationError

from ai_incident_investigator.approvals import approvals_path, report_hash
from ai_incident_investigator.models.execution import ExecutionsFile, executions_path
from ai_incident_investigator.models.history import HistoryEntry, PatternMatch, entry_id_for
from ai_incident_investigator.models.report import InvestigationReport
from ai_incident_investigator.patterns import fingerprint_report, match_fingerprints

ENTRY_FILE = "entry.json"
REPORT_FILE = "report.json"
EXECUTIONS_FILE = "executions.json"
APPROVALS_FILE = "approvals.json"


class HistoryError(Exception):
    """The command's input is unusable (bad report, bad sidecar, bad path)."""


def _load_report(report_path: Path) -> tuple[InvestigationReport, str]:
    if not report_path.is_file():
        raise HistoryError(f"report not found: {report_path}")
    try:
        report = InvestigationReport.model_validate_json(report_path.read_text())
    except (OSError, ValueError) as exc:
        raise HistoryError(f"{report_path} is not a valid investigation report: {exc}") from exc
    return report, report_hash(report_path)


def _load_executions(
    report_path: Path, explicit: Path | None
) -> tuple[ExecutionsFile | None, Path | None]:
    """The executions sidecar: an explicit path must be valid; otherwise the
    conventional `<report>.executions.json` is picked up automatically so a
    verified fix cannot be silently left out of precedent."""
    path = explicit if explicit is not None else executions_path(report_path)
    if explicit is not None and not path.is_file():
        raise HistoryError(f"executions sidecar not found: {path}")
    if not path.is_file():
        return None, None
    try:
        return ExecutionsFile.model_validate_json(path.read_text()), path
    except (OSError, ValueError) as exc:
        raise HistoryError(f"{path} is not a valid executions sidecar: {exc}") from exc


def add_entry(
    history_dir: Path,
    report_path: Path,
    executions_file: Path | None = None,
    approvals_file: Path | None = None,
) -> tuple[HistoryEntry, bool]:
    """Fingerprint and copy one investigation into the store. Idempotent:
    an entry directory that already exists is left untouched (the entry id
    is content-addressed, so same id means same report bytes). Returns
    (entry, created)."""
    report, sha = _load_report(report_path)
    executions, executions_source = _load_executions(report_path, executions_file)
    fingerprint = fingerprint_report(report, sha, executions)
    entry = HistoryEntry(entry_id=entry_id_for(fingerprint), fingerprint=fingerprint)
    if approvals_file is not None and not approvals_file.is_file():
        raise HistoryError(f"approvals sidecar not found: {approvals_file}")
    approvals_source = approvals_file if approvals_file is not None else approvals_path(report_path)

    entry_dir = history_dir / entry.entry_id
    if entry_dir.exists():
        return entry, False
    # build in a temp dir and rename so a crash can never leave a partial
    # entry under the final name (a partial entry would block re-add as a
    # false no-op); a leftover temp dir is ours and incomplete - remove it
    staging = history_dir / f"{entry.entry_id}.tmp"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    (staging / REPORT_FILE).write_bytes(report_path.read_bytes())
    if executions_source is not None:
        (staging / EXECUTIONS_FILE).write_bytes(executions_source.read_bytes())
    if approvals_source.is_file():
        # provenance copy: who approved what is part of the precedent's
        # audit trail even though matching never reads it
        (staging / APPROVALS_FILE).write_bytes(approvals_source.read_bytes())
    (staging / ENTRY_FILE).write_text(entry.model_dump_json(indent=2) + "\n")
    staging.rename(entry_dir)
    return entry, True


def load_entries(history_dir: Path) -> tuple[list[HistoryEntry], list[str]]:
    """Every readable entry in the store, plus a note per skipped one."""
    if not history_dir.is_dir():
        raise HistoryError(f"history directory not found: {history_dir}")
    entries: list[HistoryEntry] = []
    notes: list[str] = []
    for entry_dir in sorted(p for p in history_dir.iterdir() if p.is_dir()):
        entry_path = entry_dir / ENTRY_FILE
        if not entry_path.is_file():
            notes.append(f"skipped {entry_dir.name}: no {ENTRY_FILE} (not a history entry?)")
            continue
        try:
            entry = HistoryEntry.model_validate_json(entry_path.read_text())
        except (OSError, ValidationError) as exc:
            notes.append(f"skipped {entry_dir.name}: corrupt {ENTRY_FILE} ({exc})")
            continue
        if entry.entry_id != entry_dir.name:
            notes.append(
                f"skipped {entry_dir.name}: entry_id {entry.entry_id!r} does not match "
                "its directory (entries are content-addressed; renaming breaks that)"
            )
            continue
        entries.append(entry)
    return entries, notes


def match_report(history_dir: Path, report_path: Path) -> tuple[list[PatternMatch], list[str]]:
    """Explainable matches for a report against the store. Read-only: works
    against an approved report without voiding anything."""
    report, sha = _load_report(report_path)
    executions, _ = _load_executions(report_path, None)
    probe = fingerprint_report(report, sha, executions)
    entries, notes = load_entries(history_dir)
    return match_fingerprints(probe, entries), notes


def _fix_lines(entry_or_match: HistoryEntry | PatternMatch, indent: str) -> list[str]:
    fixes = (
        entry_or_match.fingerprint.executed_fixes
        if isinstance(entry_or_match, HistoryEntry)
        else entry_or_match.executed_fixes
    )
    lines = []
    for fix in fixes:
        state = "on" if fix.action.on else "off"
        tag = (
            "[verified]"
            if fix.verification == "verified"
            else f"[did NOT verify: {fix.verification}]"
        )
        lines.append(
            f"{indent}{tag} {fix.action.environment}/{fix.action.flag_key} -> {state} "
            f"({fix.executed_at.isoformat()})"
        )
    return lines


def render_entries(entries: list[HistoryEntry], notes: list[str]) -> str:
    lines = [f"note: {note}" for note in notes]
    for entry in entries:
        fp = entry.fingerprint
        lines.append(
            f"{entry.entry_id}: {fp.window_start.date().isoformat()} {fp.severity} "
            f"[{', '.join(fp.services)}]" + (" deploy-correlated" if fp.deploy_correlated else "")
        )
        lines.extend(_fix_lines(entry, "  "))
    lines.append("")
    lines.append(f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'} in the history")
    return "\n".join(lines)


def render_matches(matches: list[PatternMatch], notes: list[str]) -> str:
    lines = [f"note: {note}" for note in notes]
    if not matches:
        lines.append("no prior incidents match (docs/assumptions.md, 'Pattern matching rule')")
        return "\n".join(lines)
    for rank, match in enumerate(matches, start=1):
        lines.append(f"match {rank}: {match.explanation}")
        for feature in match.matched:
            lines.append(f"  shared (+{feature.weight}): {feature.detail}")
        for difference in match.unmatched:
            lines.append(f"  differs: {difference}")
        fix_lines = _fix_lines(match, "    ")
        if fix_lines:
            lines.append("  executed there:")
            lines.extend(fix_lines)
    lines.append("")
    lines.append(
        "matches are behavioral resemblance, never a root-cause claim; "
        "only [verified] fixes are precedent"
    )
    return "\n".join(lines)
