"""Deterministic log-engine data model (SPEC §7.4, §7.6).

These types are the contract between parsing, normalization, grouping, evidence
selection, and persistence. Nothing here depends on the AI model.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# Levels and severities
# --------------------------------------------------------------------------


class LogLevel(StrEnum):
    """Normalized log level. ``UNKNOWN`` when the source did not say."""

    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    NOTICE = "NOTICE"
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"
    UNKNOWN = "UNKNOWN"

    @property
    def is_problem(self) -> bool:
        """True when this level indicates something needs attention."""
        return self in {LogLevel.WARN, LogLevel.ERROR, LogLevel.FATAL}


class Severity(StrEnum):
    """Deterministic severity estimate (SPEC §7.7).

    This is an estimate derived from evidence, not a guarantee of business
    impact. Ordering is meaningful and used for ranking.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        """Numeric rank; higher sorts first."""
        return _SEVERITY_RANK[self]


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.CRITICAL: 5,
    Severity.HIGH: 4,
    Severity.MEDIUM: 3,
    Severity.LOW: 2,
    Severity.INFO: 1,
}


class Runtime(StrEnum):
    """Log source format. ``AUTO`` lets detection decide."""

    AUTO = "auto"
    PYTHON = "python"
    NODEJS = "nodejs"
    DOTNET = "dotnet"
    JSONL = "jsonl"
    GENERIC = "generic"


# --------------------------------------------------------------------------
# Parsed events
# --------------------------------------------------------------------------


class LogEvent(BaseModel):
    """A single parsed log event (SPEC §7.4).

    A single event may span many physical lines (a Python traceback, a JS stack).
    ``line_number`` is the first line; ``end_line_number`` the last. Any field
    that could not be extracted is ``None`` — values are never invented.
    """

    model_config = ConfigDict(frozen=True)

    line_number: int
    end_line_number: int = Field(default=0)

    timestamp: datetime | None = None
    level: LogLevel = LogLevel.UNKNOWN
    service: str | None = None
    logger: str | None = None

    message: str
    exception_type: str | None = None
    exception_message: str | None = None
    stack_trace: str | None = None

    raw_text: str
    redacted_text: str | None = None
    warnings: tuple[str, ...] = ()

    # Populated during normalization/grouping, not parsing.
    fingerprint: str | None = None
    normalized_message: str | None = None

    def __post_init__(self) -> None:
        """Default the end line to the start line when not supplied."""
        if not self.end_line_number:
            object.__setattr__(self, "end_line_number", self.line_number)

    @property
    def span(self) -> int:
        """Number of physical lines this event occupies."""
        return max(1, self.end_line_number - self.line_number + 1)

    @property
    def evidence_id(self) -> str:
        """Stable citation identifier the model must use to reference this event."""
        return f"line-{self.line_number}"


class ErrorGroup(BaseModel):
    """A cluster of recurring, fingerprint-identical errors (SPEC §7.6)."""

    model_config = ConfigDict(frozen=True)

    id: str
    fingerprint: str

    title: str
    exception_type: str | None = None
    severity: Severity = Severity.MEDIUM
    count: int = 1

    first_seen: datetime | None = None
    last_seen: datetime | None = None

    #: Deterministic explanation of how severity was derived.
    severity_reason: str = ""

    services: tuple[str, ...] = ()
    line_numbers: tuple[int, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    representative_event: LogEvent
    representative_stack: str | None = None

    @property
    def first_evidence_id(self) -> str:
        """Citation id of the representative event."""
        return self.representative_event.evidence_id


class EvidenceItem(BaseModel):
    """A selected excerpt sent to the model and cited in findings (SPEC §7.8)."""

    model_config = ConfigDict(frozen=True)

    id: str
    group_id: str | None
    line_number: int
    text: str
    kind: str  # "event" | "context"
    level: LogLevel = LogLevel.UNKNOWN


class EvidenceBundle(BaseModel):
    """The bounded evidence set selected for one investigation."""

    model_config = ConfigDict(frozen=True)

    items: tuple[EvidenceItem, ...] = ()
    total_chars: int = 0
    truncated: bool = False
    #: Human-readable note when sampling occurred, surfaced in the report.
    note: str | None = None

    def by_id(self) -> dict[str, EvidenceItem]:
        """Index the bundle for O(1) citation validation."""
        return {item.id: item for item in self.items}


class DeterministicSummary(BaseModel):
    """Counts derived purely from parsing and grouping (SPEC §6.6)."""

    model_config = ConfigDict(frozen=True)

    total_lines: int = 0
    total_events: int = 0
    error_group_count: int = 0
    warning_group_count: int = 0
    runtime: Runtime = Runtime.AUTO
    detected_runtime: Runtime = Runtime.GENERIC


# Re-export for convenience in type hints.
JsonDict = dict[str, Any]
