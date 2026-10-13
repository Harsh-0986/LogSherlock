"""Deterministic severity ranking (SPEC §7.7).

Severity is assigned by explicit rules and every assignment carries a
human-readable ``severity_reason`` so the ranking stays explainable.

This is an *estimate based on available evidence*, not a guarantee of business
impact. The model may add assessment but must not silently overwrite it.
"""

from __future__ import annotations

import re

from app.logengine.models import ErrorGroup, LogEvent, LogLevel, Severity

# Explicit fatal conditions. Each entry pairs a pattern with the explanation shown
# to the user. The pattern itself is developer-facing and must never be shown.
_CRITICAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)\b(out of memory|oom|cannot allocate memory|disk (?:is )?full|"
            r"no space left on device|panic|fatal error|unrecoverable)\b"
        ),
        "A resource was exhausted or the process could not continue",
    ),
    (
        re.compile(r"(?i)\b(segmentation fault|core dumped|access violation|stack overflow)\b"),
        "The process faulted at the operating-system level",
    ),
    (
        re.compile(
            r"(?i)\b(service unavailable|503 service unavailable"
            r"|all (?:replicas|workers) (?:are )?down)\b"
        ),
        "The service reported itself unavailable",
    ),
)

# Failed critical dependencies.
_HIGH_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)\b(connection (?:refused|reset|closed|failed)|could not connect|"
            r"unable to connect|connection (?:pool )?exhausted|timeout (?:expired)?)\b"
        ),
        "A connection to a dependency was refused or timed out",
    ),
    (
        re.compile(
            r"(?i)\b(sql|database|postgres|postgresql|mysql|mongo|redis)\b"
            r".*\b(error|failed|refused)\b"
        ),
        "A database or cache operation failed",
    ),
    (
        re.compile(
            r"(?i)\b(unhandled (?:exception|rejection|promise)|unhandled promise rejection)\b"
        ),
        "An exception was not handled by the application",
    ),
    (
        re.compile(r"(?i)\b(deadlock|lock wait timeout|cannot start a transaction)\b"),
        "A database lock or transaction could not be acquired",
    ),
)

# Count thresholds for repeated occurrences.
_CRITICAL_COUNT = 50
_HIGH_COUNT = 10

# Exceptions that are noisy and rarely represent a real outage.
_BENIGN_EXCEPTIONS: tuple[str, ...] = (
    "cancellation",
    "aborterror",
    "asyncio.CancelledError",
    "keyboardinterrupt",
    "systemexit",
)


def _match_reason(
    text: str,
    patterns: tuple[tuple[re.Pattern[str], str], ...],
) -> str | None:
    """Return the user-facing reason for the first matching pattern, or None."""
    for pattern, reason in patterns:
        if pattern.search(text):
            return reason
    return None


def assign_severity(group_events: list[LogEvent]) -> tuple[Severity, str]:
    """Assign a deterministic severity to one cluster of events.

    Returns the severity and the reason it was chosen.
    """
    if not group_events:
        return Severity.INFO, "No events in group"

    count = len(group_events)
    highest = max((e.level for e in group_events), key=_level_weight)
    haystack = " ".join(
        f"{e.message} {e.exception_type or ''} {e.exception_message or ''}" for e in group_events
    )
    exception_types = {e.exception_type for e in group_events if e.exception_type}

    # --- Critical ------------------------------------------------------
    if highest is LogLevel.FATAL:
        return Severity.CRITICAL, f"Fatal-level log events ({count} occurrences)"

    critical_reason = _match_reason(haystack, _CRITICAL_PATTERNS)
    if critical_reason is not None:
        return Severity.CRITICAL, critical_reason

    if count >= _CRITICAL_COUNT and highest is LogLevel.ERROR:
        return (
            Severity.CRITICAL,
            f"High recurrence: {count} occurrences of an error-level condition",
        )

    # --- High ----------------------------------------------------------
    if any(t.lower() in _BENIGN_EXCEPTIONS for t in exception_types):
        return Severity.LOW, "Benign/cancellation exception type"

    high_reason = _match_reason(haystack, _HIGH_PATTERNS)
    if high_reason is not None:
        return Severity.HIGH, high_reason

    if any(
        et and et.lower().endswith(("timeouterror", "connectionerror", "connectionrefused"))
        for et in exception_types
    ):
        return Severity.HIGH, f"Connection/timeout exception type: {sorted(exception_types)[0]}"

    if count >= _HIGH_COUNT and highest is LogLevel.ERROR:
        return Severity.HIGH, f"Recurring error: {count} occurrences"

    # --- Medium --------------------------------------------------------
    if highest is LogLevel.ERROR:
        return Severity.MEDIUM, "Error-level events without stronger indicators"

    if highest is LogLevel.FATAL:  # pragma: no cover - handled above
        return Severity.MEDIUM, "Fatal level"

    # --- Low -----------------------------------------------------------
    if highest is LogLevel.WARN:
        return Severity.LOW, "Warning-level events"

    # --- Info ----------------------------------------------------------
    if highest in {LogLevel.INFO, LogLevel.NOTICE}:
        return Severity.INFO, "Informational events"
    if highest is LogLevel.DEBUG:
        return Severity.INFO, "Debug-level events"

    return Severity.LOW, "Unclassified events; defaulting to low"


def _level_weight(level: LogLevel) -> int:
    """Numeric weight so ``max`` picks the most severe level."""
    return {
        LogLevel.TRACE: 0,
        LogLevel.DEBUG: 1,
        LogLevel.INFO: 2,
        LogLevel.NOTICE: 2,
        LogLevel.WARN: 3,
        LogLevel.ERROR: 4,
        LogLevel.FATAL: 5,
        LogLevel.UNKNOWN: 0,
    }[level]


def rank_groups(groups: list[ErrorGroup]) -> list[ErrorGroup]:
    """Sort groups by severity, then frequency, then recency.

    Deterministic and stable: ties break on the fingerprint so ordering does not
    depend on dict or filesystem ordering.
    """
    return sorted(
        groups,
        key=lambda g: (
            -g.severity.rank,
            -g.count,
            -(g.last_seen.timestamp() if g.last_seen else 0.0),
            g.fingerprint,
        ),
    )
