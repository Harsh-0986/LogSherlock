"""ASP.NET / .NET log parser (SPEC §7.3).

Recognises:
- ASP.NET Core default console format and IIS/W3C styles
- .NET log levels (Trace/Debug/Information/Warning/Error/Critical)
- exception type + message (``System.Data.SqlClient.SqlException: ...``)
- ``at Namespace.Type.Method(...) in file:line NN`` stack frames
- inner exceptions (``---> Inner Exception``)
"""

from __future__ import annotations

import re

from app.logengine.models import LogEvent, LogLevel
from app.logengine.parsers.base import LogParser
from app.logengine.parsers.timestamps import extract_timestamp

_LEVELS = {
    "TRACE": LogLevel.TRACE,
    "DBG": LogLevel.DEBUG,
    "DEBUG": LogLevel.DEBUG,
    "INF": LogLevel.INFO,
    "INFORMATION": LogLevel.INFO,
    "INFO": LogLevel.INFO,
    "NOTICE": LogLevel.INFO,
    "WRN": LogLevel.WARN,
    "WARN": LogLevel.WARN,
    "WARNING": LogLevel.WARN,
    "ERR": LogLevel.ERROR,
    "ERROR": LogLevel.ERROR,
    "FAIL": LogLevel.ERROR,
    "FTL": LogLevel.FATAL,
    "FATAL": LogLevel.FATAL,
    "CRIT": LogLevel.FATAL,
    "CRITICAL": LogLevel.FATAL,
}

# ASP.NET Core default: fail: Category[0]
_ASP_CATEGORY = re.compile(
    r"^(?P<level>trace|dbug|inf|wrn|err|crit)\s*:\s*(?P<logger>[\w.]+)(?:\[(?P<ctx>\d+)\])?\s*$",
    re.I,
)

# W3C / IIS: 2026-10-13 09:30:00,123 ERROR CategoryName message
_W3C = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ][\d:.,\-\+]+)\s+"
    r"(?P<level>Trace|Debug|Information|Warning|Error|Critical)\s+"
    r"(?P<logger>[\w.]+)?\s*(?P<msg>.*)$"
)

# Generic: LEVEL CategoryName - message
_SIMPLE = re.compile(
    r"^(?P<level>Trace|Debug|Information|Warn|Warning|Error|Critical)\s*[:\-]?\s*(?P<msg>.*)$",
    re.I,
)

# System.Exception: message  (namespaced exception type)
# Tolerates an HRESULT between the type and the colon, e.g.
#   System.Data.SqlClient.SqlException (0x80131904): A network-related ...
_EXCEPTION_LINE = re.compile(
    r"^(?P<type>(?:[A-Za-z_][\w]*\.)*[A-Za-z_][\w]*(?:Exception|Error|Fault|TimeoutException))"
    r"(?:\s*\(\s*0x[0-9A-Fa-f]+\s*\))?"
    r"(?::\s*(?P<msg>.*))?$"
)

# at Namespace.Type.Method(args) in /path/file.cs:line 42
_DOTNET_FRAME = re.compile(
    r"^\s*at\s+(?P<member>[A-Za-z_][\w.`]*)\s*\([^)]*\)(?:\s+in\s+(?P<file>[^\s:]+):(?P<line>\d+))?"
)

_INNER_MARKER = "--- End of inner exception stack trace ---"
_BEGIN_MARKER = re.compile(r"^Unhandled exception\. |^Server Error in ")
_ABORT_MARKER = re.compile(r"^Stack Trace:\s*$|^Exception Details:\s*$")


class DotNetParser(LogParser):
    """Parser for ASP.NET and .NET application logs."""

    runtime = "dotnet"

    def can_parse(self, text: str) -> bool:
        """True when .NET-specific markers appear in the sample."""
        head = "\n".join(text.splitlines()[:80])
        if _DOTNET_FRAME.search(head) is not None:
            return True
        if "System." in head and re.search(r"\w+Exception:", head):
            return True
        return _ASP_CATEGORY.search(head.splitlines()[0] if head.splitlines() else "") is not None

    def parse(self, text: str) -> list[LogEvent]:
        """Parse .NET log output into events."""
        lines = text.splitlines()
        events: list[LogEvent] = []
        index = 0

        while index < len(lines):
            line = lines[index]
            if not line.strip():
                index += 1
                continue

            start = index + 1
            level, logger, message, ts_token = self._match_prefix(line)

            body = [line]
            end = start
            cursor = index + 1
            while cursor < len(lines):
                nxt = lines[cursor]
                if not nxt.strip():
                    break
                if self._is_body_line(nxt):
                    body.append(nxt)
                    end = cursor + 1
                    cursor += 1
                    continue
                break

            raw_text = "\n".join(body)
            exception_type, exception_message = self._extract_exception(body)
            timestamp = extract_timestamp(ts_token) if ts_token else extract_timestamp(line)

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
        match = _W3C.match(line)
        if match is not None:
            return (
                _LEVELS.get(match["level"].upper(), LogLevel.UNKNOWN),
                match["logger"],
                (match["msg"] or "").strip() or line.strip(),
                match["ts"],
            )

        match = _ASP_CATEGORY.match(line.strip())
        if match is not None:
            return (
                _LEVELS.get(match["level"].upper(), LogLevel.UNKNOWN),
                match["logger"],
                line.strip(),
                None,
            )

        match = _SIMPLE.match(line.strip())
        if match is not None:
            level = _LEVELS.get(match["level"].upper(), LogLevel.UNKNOWN)
            if level is not LogLevel.UNKNOWN:
                return level, None, (match["msg"] or "").strip() or line.strip(), None

        return LogLevel.UNKNOWN, None, line.strip(), None

    @staticmethod
    def _is_body_line(line: str) -> bool:
        """True when the line belongs to an exception block."""
        stripped = line.strip()
        return (
            _DOTNET_FRAME.match(line) is not None
            or _EXCEPTION_LINE.match(stripped) is not None
            # "---> System.Net.Sockets.SocketException (111): Connection refused"
            or stripped.startswith("--->")
            or stripped.startswith(_INNER_MARKER)
            or _ABORT_MARKER.match(stripped) is not None
            or stripped.startswith("at ")
        )

    @staticmethod
    def _extract_exception(body: list[str]) -> tuple[str | None, str | None]:
        """Find the outermost exception; inner ones are chained by ``--->``."""
        exception_type: str | None = None
        exception_message: str | None = None
        for raw in body:
            stripped = raw.strip()
            if stripped.startswith(_INNER_MARKER) or stripped.startswith("at "):
                continue
            match = _EXCEPTION_LINE.match(stripped)
            if match is not None:
                exception_type = match["type"]
                exception_message = (match["msg"] or "").strip() or None
        return exception_type, exception_message
