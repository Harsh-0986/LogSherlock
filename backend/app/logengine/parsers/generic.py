"""Generic parser — the always-available fallback (SPEC §7.3).

Handles unrecognised formats without failing the investigation. Detects
timestamps, severity words, and service-ish tokens; attaches indented
continuation lines (tracebacks, stacks) to the preceding event.
"""

from __future__ import annotations

import re

from app.logengine.models import LogEvent, LogLevel
from app.logengine.parsers.base import LogParser
from app.logengine.parsers.timestamps import extract_epoch, extract_timestamp

# Severity words, longest/most specific first.
_LEVEL_PATTERNS: tuple[tuple[re.Pattern[str], LogLevel], ...] = (
    (re.compile(r"\b(?:CRITICAL|CRIT|FATAL|EMERG(?:ENCY)?|SEVERE|ALERT)\b", re.I), LogLevel.FATAL),
    (re.compile(r"\b(?:ERROR|ERR|SEVERE_ERROR|EXCEPTION|UNCAUGHT)\b", re.I), LogLevel.ERROR),
    (re.compile(r"\b(?:WARN(?:ING)?|DEPRECATION)\b", re.I), LogLevel.WARN),
    (re.compile(r"\b(?:INFO|INFORMATION|NOTICE)\b", re.I), LogLevel.INFO),
    (re.compile(r"\b(?:DEBUG|TRACE|VERBOSE)\b", re.I), LogLevel.DEBUG),
)

# [service] / [api] / service=api  — only treated as a service when short.
_SERVICE = re.compile(
    r"^\s*(?:\[|\()?\s*(?:service|svc|app|component)\s*[:=]\s*([\w.\-]{1,40})\b", re.I
)
_BRACKET_SERVICE = re.compile(r"^\s*\[([\w.\-]{1,40})\]\s*")

_CONTINUATION = re.compile(r"^\s*(?:at\s+|File\s+\"|Traceback\b|\s*at\s+\w+\(|---|\||\^+)")


def detect_level(text: str) -> LogLevel:
    """Return the most specific level word found in ``text``."""
    for pattern, level in _LEVEL_PATTERNS:
        if pattern.search(text):
            return level
    return LogLevel.UNKNOWN


class GenericParser(LogParser):
    """Best-effort line-oriented parser for unknown formats."""

    runtime = "generic"

    def can_parse(self, text: str) -> bool:
        """The generic parser accepts anything."""
        return True

    def parse(self, text: str) -> list[LogEvent]:
        """Split ``text`` into events, attaching continuation lines."""
        lines = text.splitlines()
        events: list[LogEvent] = []

        index = 0
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()

            if not stripped:
                index += 1
                continue

            start = index + 1  # 1-based
            level = detect_level(stripped)
            timestamp = extract_timestamp(line) or extract_epoch(line)

            # Gather continuation lines belonging to this event.
            body = [line]
            end = start
            cursor = index + 1
            while cursor < len(lines):
                nxt = lines[cursor]
                if not nxt.strip():
                    break
                if not _CONTINUATION.match(nxt) and not nxt.startswith((" ", "\t")):
                    break
                body.append(nxt)
                end = cursor + 1
                cursor += 1

            raw_text = "\n".join(body)
            service = _extract_service(stripped)

            events.append(
                LogEvent(
                    line_number=start,
                    end_line_number=end,
                    timestamp=timestamp,
                    level=level,
                    service=service,
                    message=stripped,
                    raw_text=raw_text,
                )
            )
            index = cursor

        return events


def _extract_service(line: str) -> str | None:
    """Pull a service/component name out of a line, if one is clearly marked."""
    match = _SERVICE.match(line)
    if match is not None:
        return match.group(1)
    match = _BRACKET_SERVICE.match(line)
    if match is not None:
        return match.group(1)
    return None
