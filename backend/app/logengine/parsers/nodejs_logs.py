"""Node.js / JavaScript log parser (SPEC §7.3).

Recognises:
- ``pino``-style single-line JSON (level, time, msg, stack, err)
- common timestamped console formats
- ``Error: message`` lines and their ``name`` / ``message`` / ``stack`` fields
- multiline JS stack frames (``at fn (file:line:col)``)
- common severity labels (error/warn/info/debug)
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.logengine.models import LogEvent, LogLevel
from app.logengine.parsers.base import LogParser
from app.logengine.parsers.timestamps import extract_timestamp

# pino/winston level number -> level name
_NUMERIC_LEVELS: dict[int, LogLevel] = {
    10: LogLevel.TRACE,
    20: LogLevel.DEBUG,
    30: LogLevel.INFO,
    40: LogLevel.WARN,
    50: LogLevel.ERROR,
    60: LogLevel.FATAL,
}

_STRING_LEVELS = {
    "trace": LogLevel.TRACE,
    "debug": LogLevel.DEBUG,
    "verbose": LogLevel.DEBUG,
    "info": LogLevel.INFO,
    "warn": LogLevel.WARN,
    "warning": LogLevel.WARN,
    "error": LogLevel.ERROR,
    "err": LogLevel.ERROR,
    "fatal": LogLevel.FATAL,
    "critical": LogLevel.FATAL,
    "crit": LogLevel.FATAL,
}

_LEVEL_WORD = re.compile(r"\b(trace|debug|verbose|info|warn|warning|error|fatal|critical)\b", re.I)

# Node's Error prefix: Name: message. The line may be prefixed by a timestamp
# and severity, so this is searched rather than anchored.
_ERROR_LINE = re.compile(
    r"(?:^|[\s\]])(?P<type>(?:[A-Z][A-Za-z0-9_]*)?"
    r"(?:Error|Exception|Fault|Timeout))\s*:\s*(?P<msg>\S.*)$"
)

# stack frame:  at foo (path/file.js:12:5)  |  at Object.<anonymous> (/a/b.js:1:1)
_STACK_FRAME = re.compile(r"^[ \t]*at\s+.+", re.MULTILINE)

_COMMON_TIME_PREFIX = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2}[T ][\d:.]+(?:Z|[+\-]\d{2}:?\d{2})?)\]\s*"
)

# Bare leading timestamp: "2026-10-13T09:15:44.019Z ERROR [svc] ..."
_BARE_TIME_PREFIX = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?"
    r"(?:Z|[+\-]\d{2}:?\d{2})?\s*"
)

# Leading severity word: "ERROR", "WARN", "INFO " — dropped from the message so
# grouping keys stay stable and titles read cleanly.
_LEADING_LEVEL = re.compile(
    r"^(?:trace|debug|verbose|info|warn|warning|error|fatal|critical)\b[:\s]*", re.I
)

# "service=orders-api " style key-value prefix is noise in a title.
_SERVICE_KV_PREFIX = re.compile(r"\bservice=\S+\s*", re.I)


def level_from(value: Any) -> LogLevel:
    """Map a pino/winston level value to a LogLevel."""
    if isinstance(value, bool):
        return LogLevel.UNKNOWN
    if isinstance(value, int):
        return _NUMERIC_LEVELS.get(value, LogLevel.UNKNOWN)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _STRING_LEVELS:
            return _STRING_LEVELS[text]
        if text.isdigit():
            return _NUMERIC_LEVELS.get(int(text), LogLevel.UNKNOWN)
    return LogLevel.UNKNOWN


#: A JS stack frame: "at fn (/path/file.js:12:5)". Deliberately requires a
#: .js location so Python's `File "...", line N, in fn` never matches here.
_JS_FRAME = re.compile(r"^[ \t]*at\s+[\w$.<>\[\]]+\s+\([^)]*\.js:\d+", re.MULTILINE)

_NODE_ONLY_MARKERS = ("UnhandledPromiseRejection", "Unhandled 'error' event", "ECONNRESET")


class NodeParser(LogParser):
    """Parser for Node.js console/pino/winston output."""

    runtime = "nodejs"

    def can_parse(self, text: str) -> bool:
        """True when Node-specific markers appear in the sample.

        Deliberately strict: a bare ``SomethingError: message`` line also occurs
        in Python logs, so it is not on its own evidence for this parser.
        """
        head = "\n".join(text.splitlines()[:80])

        for line in head.splitlines():
            stripped = line.strip()
            if stripped.startswith("{") and '"level"' in stripped:
                return True

        if _JS_FRAME.search(head):
            return True

        return any(marker in head for marker in _NODE_ONLY_MARKERS)

    def parse(self, text: str) -> list[LogEvent]:
        """Parse Node.js log output into events."""
        lines = text.splitlines()
        events: list[LogEvent] = []
        index = 0

        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            if not stripped:
                index += 1
                continue

            start = index + 1

            if stripped.startswith("{") and '"level"' in stripped:
                parsed = self._parse_structured(line, start)
                if parsed is not None:
                    events.append(parsed)
                    index += 1
                    continue

            # Header line + following stack frames / continuation
            level = self._level_from_header(stripped)
            body = [line]
            end = start
            cursor = index + 1
            while cursor < len(lines):
                nxt = lines[cursor]
                if not nxt.strip():
                    break
                if _STACK_FRAME.match(nxt) or _ERROR_LINE.search(nxt):
                    body.append(nxt)
                    end = cursor + 1
                    cursor += 1
                    continue
                break

            raw_text = "\n".join(body)
            exception_type, exception_message = self._extract_error(body)
            timestamp = extract_timestamp(line)
            message = self._strip_timestamp_prefix(stripped)

            if exception_type is not None and level in {LogLevel.UNKNOWN, LogLevel.INFO}:
                level = LogLevel.ERROR

            events.append(
                LogEvent(
                    line_number=start,
                    end_line_number=end,
                    timestamp=timestamp,
                    level=level,
                    message=message,
                    exception_type=exception_type,
                    exception_message=exception_message,
                    stack_trace=raw_text if exception_type else None,
                    raw_text=raw_text,
                )
            )
            index = cursor

        return events

    # -- internals --------------------------------------------------------

    def _parse_structured(self, line: str, start: int) -> LogEvent | None:
        """Parse a pino/winston single-line JSON record."""
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None

        level = level_from(data.get("level", data.get("lvl")))
        err = data.get("err") if isinstance(data.get("err"), dict) else {}
        message = str(data.get("msg") or data.get("message") or err.get("message") or "").strip()
        exception_type = err.get("type") or data.get("type")
        exception_message = err.get("message") or data.get("message")
        stack = err.get("stack") or data.get("stack")

        ts = data.get("time") or data.get("timestamp") or data.get("ts")
        timestamp = None
        if isinstance(ts, str):
            timestamp = extract_timestamp(ts)
        elif isinstance(ts, (int, float)):
            from datetime import UTC, datetime

            try:
                timestamp = datetime.fromtimestamp(ts / 1000, tz=UTC)
            except (OverflowError, OSError, ValueError):
                timestamp = None

        service = data.get("service") or data.get("name") or data.get("component")

        return LogEvent(
            line_number=start,
            end_line_number=start,
            timestamp=timestamp,
            level=level,
            service=str(service) if isinstance(service, str) else None,
            message=message or line.strip(),
            exception_type=str(exception_type) if isinstance(exception_type, str) else None,
            exception_message=(
                str(exception_message) if isinstance(exception_message, str) else None
            ),
            stack_trace=str(stack) if isinstance(stack, str) else None,
            raw_text=line,
        )

    @staticmethod
    def _level_from_header(stripped: str) -> LogLevel:
        """Find a severity word near the start of a plain-text line."""
        prefix = stripped[:80]
        match = _LEVEL_WORD.search(prefix)
        if match is None:
            return LogLevel.UNKNOWN
        return _STRING_LEVELS.get(match.group(1).lower(), LogLevel.UNKNOWN)

    @staticmethod
    def _strip_timestamp_prefix(stripped: str) -> str:
        """Remove leading timestamp, severity and service tokens.

        These vary per line but carry no diagnostic signal for grouping, so
        dropping them keeps one error as one group regardless of when or where
        it was logged.
        """
        out = _BARE_TIME_PREFIX.sub("", stripped)
        out = _COMMON_TIME_PREFIX.sub("", out)
        out = _LEADING_LEVEL.sub("", out)
        out = _SERVICE_KV_PREFIX.sub("", out)
        return out.strip() or stripped

    @staticmethod
    def _extract_error(body: list[str]) -> tuple[str | None, str | None]:
        """Find the outermost ``TypeError: message`` in the body."""
        exception_type: str | None = None
        exception_message: str | None = None
        for raw in body:
            stripped = raw.strip()
            if stripped.startswith("at "):
                continue
            match = _ERROR_LINE.search(stripped)
            if match is not None:
                exception_type = match["type"]
                exception_message = match["msg"].strip() or None
        return exception_type, exception_message
