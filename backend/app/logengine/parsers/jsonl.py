"""JSONL parser — one JSON object per line (SPEC §7.1).

Malformed or non-object records must be handled safely: they degrade to
generic events carrying a warning rather than failing the investigation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.logengine.models import LogEvent, LogLevel
from app.logengine.parsers.base import LogParser
from app.logengine.parsers.nodejs_logs import level_from

# Field names seen across common logging libraries, in priority order.
_LEVEL_KEYS = ("level", "severity", "lvl", "log.level", "levelname", "levelName")
_MESSAGE_KEYS = ("message", "msg", "event", "log", "@message", "short_message")
_TIME_KEYS = ("timestamp", "time", "ts", "@timestamp", "time_stamp", "eventTime", "asctime")
_SERVICE_KEYS = ("service", "service_name", "app", "application", "component", "source")
_ERROR_KEYS = ("err", "error", "exception", "error_type", "exceptionType")
_STACK_KEYS = ("stack", "stacktrace", "stack_trace", "traceback")


class JsonlParser(LogParser):
    """Parser for newline-delimited JSON records."""

    runtime = "jsonl"

    def can_parse(self, text: str) -> bool:
        """True when a majority of the first lines are JSON objects."""
        lines = [ln for ln in text.splitlines()[:50] if ln.strip()]
        if not lines:
            return False
        objects = 0
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    if isinstance(json.loads(stripped), dict):
                        objects += 1
                except json.JSONDecodeError:
                    pass
        return objects >= max(1, len(lines) // 2)

    def parse(self, text: str) -> list[LogEvent]:
        """Parse JSONL, degrading malformed records to generic events."""
        events: list[LogEvent] = []
        for index, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue

            if not (stripped.startswith("{") and stripped.endswith("}")):
                events.append(self._malformed(index, line))
                continue

            try:
                data = json.loads(stripped)
            except json.JSONDecodeError as exc:
                events.append(self._malformed(index, line, str(exc)))
                continue

            if not isinstance(data, dict):
                events.append(self._malformed(index, line, "record is not a JSON object"))
                continue

            events.append(self._to_event(data, index, line))

        return events

    # -- internals --------------------------------------------------------

    def _to_event(self, data: dict[str, Any], line_number: int, raw: str) -> LogEvent:
        """Build an event from a decoded JSON object."""
        level = LogLevel.UNKNOWN
        for key in _LEVEL_KEYS:
            if key in data:
                level = level_from(data[key])
                if level is not LogLevel.UNKNOWN:
                    break

        message = self._first_string(data, _MESSAGE_KEYS) or ""

        error_obj = self._first_dict(data, _ERROR_KEYS)
        exception_type: str | None = None
        exception_message: str | None = None
        if error_obj is not None:
            exception_type = self._first_string(
                error_obj, ("type", "name", "class", "exceptionType")
            )
            exception_message = self._first_string(error_obj, ("message", "msg", "reason"))
        if exception_type is None:
            exception_type = self._first_string(
                data, ("exception_type", "exceptionType", "error_type")
            )
        if exception_message is None:
            exception_message = self._first_string(data, ("exception_message", "errorMessage"))

        stack = self._first_string(data, _STACK_KEYS)
        if stack is None and error_obj is not None:
            stack = self._first_string(error_obj, _STACK_KEYS)

        service = self._first_string(data, _SERVICE_KEYS)
        timestamp = self._first_time(data)

        if not message:
            message = exception_message or exception_type or raw.strip()

        if exception_type is not None and level in {LogLevel.UNKNOWN, LogLevel.INFO}:
            level = LogLevel.ERROR

        warnings: tuple[str, ...] = ()
        if message == raw.strip() and not any(
            data.get(k) is not None for k in (*_MESSAGE_KEYS, *_ERROR_KEYS)
        ):
            warnings = ("no recognised message field; used raw record",)

        return LogEvent(
            line_number=line_number,
            end_line_number=line_number,
            timestamp=timestamp,
            level=level,
            service=service,
            message=message,
            exception_type=exception_type,
            exception_message=exception_message,
            stack_trace=stack,
            raw_text=raw,
            warnings=warnings,
        )

    def _malformed(self, line_number: int, raw: str, detail: str | None = None) -> LogEvent:
        """Build a safe fallback event for a record we could not decode."""
        from app.logengine.parsers.generic import detect_level
        from app.logengine.parsers.timestamps import extract_timestamp

        message = raw.strip()
        return LogEvent(
            line_number=line_number,
            end_line_number=line_number,
            timestamp=extract_timestamp(raw),
            level=detect_level(message),
            message=message,
            raw_text=raw,
            warnings=(f"malformed JSONL record: {detail or 'not valid JSON'}",),
        )

    @staticmethod
    def _first_string(data: dict[str, Any], keys: tuple[str, ...]) -> str | None:
        """Return the first non-empty string value among ``keys``."""
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _first_dict(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any] | None:
        """Return the first dict value among ``keys``."""
        for key in keys:
            value = data.get(key)
            if isinstance(value, dict):
                return value
        return None

    @staticmethod
    def _first_time(data: dict[str, Any]) -> datetime | None:
        """Extract a timestamp from string/epoch forms."""
        from app.logengine.parsers.timestamps import extract_timestamp

        for key in _TIME_KEYS:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                parsed = extract_timestamp(value.strip())
                if parsed is not None:
                    return parsed
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                # Heuristic: ms vs s.
                seconds = value / 1000 if value > 1e11 else float(value)
                try:
                    return datetime.fromtimestamp(seconds, tz=UTC)
                except (OverflowError, OSError, ValueError):
                    continue
        return None
