"""Timestamp extraction helpers shared by the text parsers.

Deliberately conservative: when a timestamp cannot be parsed confidently the
result is ``None``. Inventing a timestamp would corrupt grouping and ordering,
and SPEC §7.4 forbids it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}

_ISO = re.compile(
    r"(?P<y>\d{4})[-/](?P<mo>\d{2})[-/](?P<d>\d{2})[T ](?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})"
    r"(?:[.,](?P<frac>\d{1,9}))?(?P<tz>Z|z|[+\-]\d{2}:?\d{2})?"
)

_APACHE = re.compile(
    r"(?P<d>\d{2})/(?P<mon>[A-Za-z]{3})/(?P<y>\d{4}):(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})"
    r"(?:\s+(?P<tz>[+\-]\d{4}))?"
)

_US = re.compile(
    r"(?P<mo>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{4})\s+(?P<h>\d{1,2}):(?P<mi>\d{2}):(?P<s>\d{2})"
    r"(?:\s*(?P<ampm>[AaPp][Mm]))?"
)

# Bare epoch seconds/millis — only used when nothing else matched.
_EPOCH = re.compile(r"\b(?P<epoch>\d{10}|\d{13})\b")


def _tzinfo(raw: str | None) -> timezone:
    """Convert a ``Z`` / ``+0200`` / ``-05:00`` token into a tzinfo (default UTC)."""
    if not raw or raw in {"Z", "z"}:
        return UTC
    sign = 1 if raw[0] == "+" else -1
    digits = raw[1:].replace(":", "")
    hours = int(digits[:2])
    minutes = int(digits[2:4]) if len(digits) >= 4 else 0
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _assemble(
    *,
    year: str,
    month: int,
    day: str,
    hour: str,
    minute: str,
    second: str,
    micro: int = 0,
    tz: timezone | None = None,
) -> datetime | None:
    """Build a datetime, returning None on any out-of-range value."""
    try:
        return datetime(
            int(year),
            month,
            int(day),
            int(hour),
            int(minute),
            int(second),
            micro,
            tz or UTC,
        )
    except (TypeError, ValueError):
        return None


def extract_timestamp(line: str) -> datetime | None:
    """Best-effort ISO timestamp extraction from a single log line."""
    if not line:
        return None

    match = _ISO.search(line)
    if match is not None:
        g = match.groupdict()
        frac = g.get("frac") or ""
        micro = int(frac.ljust(6, "0")[:6]) if frac else 0
        return _assemble(
            year=g["y"],
            month=int(g["mo"]),
            day=g["d"],
            hour=g["h"],
            minute=g["mi"],
            second=g["s"],
            micro=micro,
            tz=_tzinfo(g.get("tz")),
        )

    match = _APACHE.search(line)
    if match is not None:
        g = match.groupdict()
        month = _MONTHS.get((g["mon"] or "").lower(), 0)
        if month:
            return _assemble(
                year=g["y"],
                month=month,
                day=g["d"],
                hour=g["h"],
                minute=g["mi"],
                second=g["s"],
                tz=_tzinfo(g.get("tz")),
            )

    match = _US.search(line)
    if match is not None:
        g = match.groupdict()
        hour = int(g["h"])
        ampm = (g.get("ampm") or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        try:
            return _assemble(
                year=g["y"],
                month=int(g["mo"]),
                day=g["d"],
                hour=str(hour),
                minute=g["mi"],
                second=g["s"],
            )
        except ValueError:
            return None

    return None


def extract_epoch(line: str) -> datetime | None:
    """Extract a bare epoch timestamp, if one is present."""
    match = _EPOCH.search(line)
    if match is None:
        return None
    raw = match.group("epoch")
    try:
        value = int(raw)
    except ValueError:
        return None
    # Heuristic: 13 digits is milliseconds, 10 digits is seconds.
    if len(raw) == 13:
        value /= 1000
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
