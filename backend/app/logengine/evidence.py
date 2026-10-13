"""Evidence selection (SPEC §7.8).

Chooses which log lines are sent to the model. The whole input is never sent by
default: a bounded, char-budgeted selection is produced, and any sampling or
truncation is disclosed rather than silent.
"""

from __future__ import annotations

from app.logengine.models import (
    ErrorGroup,
    EvidenceBundle,
    EvidenceItem,
    LogEvent,
    LogLevel,
)
from app.logengine.redaction import Redactor

#: Extra non-error lines kept around each representative for context.
CONTEXT_LINES = 2

#: Additional occurrences of a group included beyond the representative.
MAX_EXTRA_PER_GROUP = 3


def select_evidence(
    *,
    events: list[LogEvent],
    groups: list[ErrorGroup],
    redactor: Redactor,
    max_chars: int = 16_000,
    max_groups: int = 8,
) -> EvidenceBundle:
    """Build a bounded, redacted evidence bundle.

    Selection order is deterministic: groups in the order given (already ranked),
    representative event first, then context lines, then extra occurrences.
    """
    by_line = {e.line_number: e for e in events}
    items: list[EvidenceItem] = []
    total = 0
    truncated = False
    notes: list[str] = []

    selected_groups = groups[:max_groups]
    if len(groups) > max_groups:
        notes.append(
            f"Only the top {max_groups} of {len(groups)} error groups were sent to the "
            "model; the remaining groups were still included deterministically in the report."
        )

    seen_ids: set[str] = set()

    for group in selected_groups:
        rep = group.representative_event

        # 1. The representative event (always redacted before leaving the process).
        item = _make_item(rep, group.id, "event", redactor)
        if item is not None and _fits(item, total, max_chars):
            items.append(item)
            seen_ids.add(item.id)
            total += len(item.text) + len(item.id) + 16
        else:
            truncated = True
            continue

        # 2. Nearby context lines (INFO/DEBUG around the failure).
        for line_no in _context_range(rep, events):
            ctx_event = by_line.get(line_no)
            if ctx_event is None or ctx_event.evidence_id in seen_ids:
                continue
            ctx_item = _make_item(ctx_event, group.id, "context", redactor)
            if ctx_item is None:
                continue
            if _fits(ctx_item, total, max_chars):
                items.append(ctx_item)
                seen_ids.add(ctx_item.id)
                total += len(ctx_item.text) + len(ctx_item.id) + 16
            else:
                truncated = True

        # 3. A few more occurrences, so the model can see the repeat pattern.
        extras = [e for e in events if e.evidence_id in group.evidence_ids]
        for extra in extras[1 : 1 + MAX_EXTRA_PER_GROUP]:
            if extra.evidence_id in seen_ids:
                continue
            extra_item = _make_item(extra, group.id, "event", redactor)
            if extra_item is None:
                continue
            if _fits(extra_item, total, max_chars):
                items.append(extra_item)
                seen_ids.add(extra_item.id)
                total += len(extra_item.text) + len(extra_item.id) + 16
            else:
                truncated = True

    if truncated:
        notes.append(
            "Evidence was truncated to fit the configured character budget; "
            "some occurrences were not shown to the model."
        )

    return EvidenceBundle(
        items=tuple(items),
        total_chars=total,
        truncated=truncated,
        note=" ".join(notes) if notes else None,
    )


def _context_range(rep: LogEvent, events: list[LogEvent]) -> list[int]:
    """Return line numbers of up to CONTEXT_LINES neighbours before the event."""
    lines = sorted(e.line_number for e in events)
    try:
        position = lines.index(rep.line_number)
    except ValueError:  # pragma: no cover - representative always present
        return []
    return lines[max(0, position - CONTEXT_LINES) : position]


def _make_item(
    event: LogEvent, group_id: str | None, kind: str, redactor: Redactor
) -> EvidenceItem | None:
    """Build a redacted evidence item for one event."""
    # Redaction happens here so redacted text — never raw — reaches the model.
    redacted = redactor.redact(event.raw_text)
    return EvidenceItem(
        id=event.evidence_id,
        group_id=group_id,
        line_number=event.line_number,
        text=redacted.text,
        kind=kind,
        level=event.level,
    )


def _fits(item: EvidenceItem, current: int, max_chars: int) -> bool:
    """Whether adding ``item`` stays within the character budget."""
    return current + len(item.text) + len(item.id) + 16 <= max_chars


def format_evidence_for_prompt(bundle: EvidenceBundle) -> str:
    """Render the evidence bundle as the text block handed to the model.

    Each line is explicitly labelled with its citation id so the model can only
    reference ids that actually exist (SPEC §9.2).
    """
    if not bundle.items:
        return "(no evidence available)"

    chunks: list[str] = []
    for item in bundle.items:
        header = f"[{item.id}] line {item.line_number} · {item.level.value}"
        if item.group_id:
            header += f" · group {item.group_id}"
        chunks.append(f"{header}\n{item.text}")

    if bundle.note:
        chunks.append(f"(note: {bundle.note})")

    return "\n\n".join(chunks)


_LEVEL_ORDER = {
    LogLevel.FATAL: 5,
    LogLevel.ERROR: 4,
    LogLevel.WARN: 3,
    LogLevel.INFO: 2,
    LogLevel.DEBUG: 1,
    LogLevel.TRACE: 0,
    LogLevel.UNKNOWN: 0,
}
