"""Error grouping (SPEC §7.6).

Clusters parsed events by fingerprint and assembles the per-group record the
report renders. Purely deterministic: the same input always yields the same
groups in the same order.
"""

from __future__ import annotations

from datetime import datetime

from app.core.ids import new_group_id
from app.logengine.fingerprint import compute_fingerprint, extract_frames
from app.logengine.models import ErrorGroup, LogEvent
from app.logengine.normalize import normalize_message, normalize_title
from app.logengine.severity import assign_severity


def group_events(events: list[LogEvent]) -> list[ErrorGroup]:
    """Group error/warning events into deterministic :class:`ErrorGroup` records.

    Only problem-level events (WARN/ERROR/FATAL) become groups; INFO and DEBUG
    events are counted in the summary but are not "error groups".
    """
    buckets: dict[str, list[LogEvent]] = {}
    normalized_by_id: dict[int, str] = {}

    for event in events:
        if not event.level.is_problem:
            continue

        normalized = normalize_message(event.message)
        frames = extract_frames(event.stack_trace)
        fingerprint = compute_fingerprint(
            exception_type=event.exception_type,
            normalized_message=normalized,
            frames=frames,
            # Service is included only when the event carries one, so a group
            # is not split merely because one log line omitted the field.
            service=event.service,
        )
        normalized_by_id[id(event)] = normalized
        buckets.setdefault(fingerprint, []).append(event)

    groups: list[ErrorGroup] = []
    for fingerprint, members in buckets.items():
        groups.append(_build_group(fingerprint, members, normalized_by_id))

    return _disambiguate_titles(groups)


def _disambiguate_titles(groups: list[ErrorGroup]) -> list[ErrorGroup]:
    """Make titles unique so equal errors from different call paths stay legible.

    Two groups can legitimately share an error message while differing in stack
    frames — for example the same database failure reached through two routes.
    Without disambiguation the report shows two identically-titled rows and
    reads as a bug. Each colliding title gains its distinguishing top frame.
    """
    by_title: dict[str, list[ErrorGroup]] = {}
    for group in groups:
        by_title.setdefault(group.title, []).append(group)

    updated: list[ErrorGroup] = []
    for group in groups:
        siblings = by_title[group.title]
        if len(siblings) < 2:
            updated.append(group)
            continue
        marker = _distinguishing_frame(group)
        if marker is None:
            updated.append(group)
            continue
        updated.append(group.model_copy(update={"title": f"{group.title} — {marker}"}))
    return updated


def _distinguishing_frame(group: ErrorGroup) -> str | None:
    """Return the most distinctive frame of a group's representative stack."""
    stack = group.representative_stack or ""
    frames = extract_frames(stack, depth=6)
    if not frames:
        return None
    # Deepest application frame is the most informative discriminator.
    for frame in reversed(frames):
        member = frame.rsplit("@", 1)[0]
        if not member.startswith(("raw:", "py:<module>")):
            return member.split(":", 1)[-1] or frame
    return frames[-1]


def _build_group(
    fingerprint: str,
    members: list[LogEvent],
    normalized_by_id: dict[int, str],
) -> ErrorGroup:
    """Assemble one ErrorGroup from its member events."""
    # Representative event: the first one carrying a stack trace, else the first.
    representative = next((e for e in members if e.stack_trace), members[0] if members else None)
    if representative is None:  # pragma: no cover - group_events never calls with []
        raise ValueError("cannot build a group from zero events")

    severity, reason = assign_severity(members)

    timestamps = [e.timestamp for e in members if e.timestamp is not None]
    first_seen: datetime | None = min(timestamps) if timestamps else None
    last_seen: datetime | None = max(timestamps) if timestamps else None

    services = tuple(sorted({e.service for e in members if e.service is not None}))

    title = normalize_title(
        representative.exception_message or representative.message,
        representative.exception_type,
    )

    return ErrorGroup(
        id=new_group_id(),
        fingerprint=fingerprint,
        title=title,
        exception_type=representative.exception_type,
        severity=severity,
        count=len(members),
        first_seen=first_seen,
        last_seen=last_seen,
        severity_reason=reason,
        services=services,
        line_numbers=tuple(e.line_number for e in members),
        evidence_ids=tuple(e.evidence_id for e in members),
        representative_event=representative,
        representative_stack=representative.stack_trace,
    )


def apply_normalized_messages(events: list[LogEvent]) -> list[LogEvent]:
    """Attach normalized messages to events.

    ``LogEvent`` is frozen, so this returns new events rather than mutating.
    Raw values are preserved unchanged (SPEC §7.5).

    Returns:
        A new list of events carrying ``normalized_message``.
    """
    return [
        event.model_copy(update={"normalized_message": normalize_message(event.message)})
        for event in events
    ]
