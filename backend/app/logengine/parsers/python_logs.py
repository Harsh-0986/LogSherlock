"""Python log parser (SPEC §7.3).

Recognises:
- standard ``logging`` formats (``LEVEL:logger:message`` and ``LEVEL logger message``)
- ``Traceback (most recent call last):`` blocks
- exception class + message on the final line
- file/line references inside ``File "...", line N, in fn``
- chained exceptions (``During handling of ...`` / ``The above exception ...``)
"""

from __future__ import annotations

import re

from app.logengine.models import LogEvent, LogLevel
from app.logengine.parsers.base import LogParser
from app.logengine.parsers.timestamps import extract_timestamp

_LEVELS = {
    "CRITICAL": LogLevel.FATAL,
    "FATAL": LogLevel.FATAL,
    "ERROR": LogLevel.ERROR,
    "WARNING": LogLevel.WARN,
    "WARN": LogLevel.WARN,
    "INFO": LogLevel.INFO,
    "DEBUG": LogLevel.DEBUG,
    "TRACE": LogLevel.TRACE,
    "NOTSET": LogLevel.UNKNOWN,
}

# 2026-10-13 09:30:00,123 - ERROR - module.name - message
_FORMAT_DASH = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ][\d:.,+\-Z ]+?)\s+-\s+"
    r"(?P<level>[A-Z]+)\s+-\s+(?P<logger>[\w.]+)\s+-\s+(?P<msg>.*)$"
)

# ERROR:logger:message
_FORMAT_COLON = re.compile(r"^(?P<level>[A-Z]+):(?P<logger>[\w.]+):(?P<msg>.*)$")

# 2026-10-13 09:30:00,123 INFO     module  message
_FORMAT_LOOSE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ][\d:.,+\-Z ]+?)\s+"
    r"(?P<level>[A-Z]+)\s+"
    r"(?P<logger>[\w.]+)?\s*[:#]?\s*"
    r"(?P<msg>.*)$"
)

_TRACEBACK_START = re.compile(r"^Traceback \(most recent call last\):\s*$")
_CHAIN_MARKERS = (
    "During handling of the above exception, another exception occurred:",
    "The above exception was the direct cause of the following exception:",
)

# Final line of a traceback: Module.SubclassError: message
_EXCEPTION_LINE = re.compile(
    r"^(?P<type>(?:[A-Za-z_][\w]*\.)*[A-Za-z_][\w]*(?:Error|Exception|Warning|Fault|Interrupt|Exit|Timeout))"
    r"(?::\s*(?P<msg>.*))?$"
)

_FILE_REF = re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>[\w<>.]+)')

_MODULE_LEVEL = re.compile(r"^(?:INFO|WARNING|ERROR|DEBUG|CRITICAL|NOTICE|TRACE|FATAL)")


class PythonParser(LogParser):
    """Parser for Python ``logging`` output and tracebacks."""

    runtime = "python"

    def can_parse(self, text: str) -> bool:
        """True when Python-specific markers are present."""
        if _TRACEBACK_START.search(text):
            return True
        if any(
            line.strip().endswith("Error:") or _EXCEPTION_LINE.match(line.strip())
            for line in text.splitlines()[:200]
        ):
            return True
        if _FORMAT_DASH.search(text.splitlines()[0] if text.splitlines() else "") is not None:
            return True
        return _MODULE_LEVEL.search(text.splitlines()[0] if text.splitlines() else "") is not None

    def parse(self, text: str) -> list[LogEvent]:
        """Parse Python log output into events."""
        lines = text.splitlines()
        events: list[LogEvent] = []
        index = 0

        while index < len(lines):
            line = lines[index]
            if not line.strip():
                index += 1
                continue

            start = index + 1
            level, logger, message, ts_match = self._match_prefix(line)

            body = [line]
            end = start
            cursor = index + 1
            in_traceback = False
            saw_chain = False

            # Absorb a traceback and every chained exception that follows.
            while cursor < len(lines):
                nxt = lines[cursor]
                stripped = nxt.strip()
                if not stripped:
                    # A blank line ends the traceback, but a chain marker may
                    # resume after it — look ahead before deciding.
                    look = cursor + 1
                    while look < len(lines) and not lines[look].strip():
                        look += 1
                    if look < len(lines) and lines[look].strip().startswith(_CHAIN_MARKERS):
                        body.append(nxt)
                        cursor += 1
                        continue
                    # Once a chained exception has been announced, the traceback
                    # continues across blank lines up to the next log record.
                    if saw_chain:
                        body.append(nxt)
                        cursor += 1
                        continue
                    break

                if stripped.startswith(_CHAIN_MARKERS):
                    saw_chain = True
                    in_traceback = True
                elif self._is_traceback_line(nxt) or _EXCEPTION_LINE.match(stripped) is not None:
                    in_traceback = True
                elif in_traceback and not self._looks_like_new_header(nxt):
                    # Source lines quoted inside a traceback frame, and any
                    # other unrecognised content while a traceback is open.
                    in_traceback = True
                else:
                    break

                body.append(nxt)
                end = cursor + 1
                cursor += 1

            raw_text = "\n".join(body)
            exception_type, exception_message = self._extract_exception(body)
            timestamp = extract_timestamp(ts_match or line)

            if exception_type is not None and level in {LogLevel.UNKNOWN, LogLevel.INFO}:
                level = LogLevel.ERROR

            events.append(
                LogEvent(
                    line_number=start,
                    end_line_number=end,
                    timestamp=timestamp,
                    level=level,
                    logger=logger,
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

    def _match_prefix(self, line: str) -> tuple[LogLevel, str | None, str, str | None]:
        """Extract (level, logger, message, timestamp-token) from a line."""
        match = _FORMAT_DASH.match(line)
        if match is not None:
            return (
                _LEVELS.get(match["level"].upper(), LogLevel.UNKNOWN),
                match["logger"],
                match["msg"].strip(),
                match["ts"],
            )

        match = _FORMAT_COLON.match(line)
        if match is not None:
            level = _LEVELS.get(match["level"].upper(), LogLevel.UNKNOWN)
            if level is not LogLevel.UNKNOWN:
                return level, match["logger"], match["msg"].strip(), None

        match = _FORMAT_LOOSE.match(line)
        if match is not None:
            level = _LEVELS.get(match["level"].upper(), LogLevel.UNKNOWN)
            if level is not LogLevel.UNKNOWN:
                return level, match["logger"], match["msg"].strip(), match["ts"]

        return LogLevel.UNKNOWN, None, line.strip(), None

    @staticmethod
    def _is_traceback_line(line: str) -> bool:
        """True when the line belongs to a traceback body."""
        stripped = line.strip()
        return (
            stripped.startswith(('File "', "Traceback", "~~~"))
            or _FILE_REF.search(line) is not None
        )

    @staticmethod
    def _looks_like_new_header(line: str) -> bool:
        """True when the line starts a *new* log record rather than continuing one."""
        if _FORMAT_DASH.match(line) is not None:
            return True
        if _FORMAT_COLON.match(line) is not None:
            return _LEVELS.get(_FORMAT_COLON.match(line)["level"].upper()) is not None
        if _FORMAT_LOOSE.match(line) is not None:
            match = _FORMAT_LOOSE.match(line)
            return _LEVELS.get(match["level"].upper()) is not None
        return False

    @staticmethod
    def _extract_exception(body: list[str]) -> tuple[str | None, str | None]:
        """Find the outermost exception type/message in a traceback body."""
        exception_type: str | None = None
        exception_message: str | None = None
        for raw in body:
            stripped = raw.strip()
            if not stripped or stripped.startswith(_CHAIN_MARKERS):
                continue
            match = _EXCEPTION_LINE.match(stripped)
            if match is not None:
                exception_type = match["type"]
                exception_message = (match["msg"] or "").strip() or None
        return exception_type, exception_message
