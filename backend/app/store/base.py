"""Persistence seam (SPEC §11).

The API and job manager depend on :class:`InvestigationStore`, not on a
database. The in-process implementation below satisfies the MVP without a
database; Phase 5 swaps in a SQLAlchemy-backed one with the same contract.
"""

from __future__ import annotations

import abc
from typing import Any

from app.logengine.models import ErrorGroup, EvidenceItem


class InvestigationRecord:
    """One stored investigation and everything attached to it."""

    __slots__ = (
        "ai_skipped_reason",
        "completed_at",
        "created_at",
        "current_step",
        "duration_seconds",
        "error_group_count",
        "error_groups",
        "error_message",
        "event_count",
        "evidence",
        "findings",
        "id",
        "model_name",
        "model_provider",
        "model_response",
        "processing_steps",
        "raw_input",
        "report",
        "retry_count",
        "runtime",
        "status",
        "summary",
        "summary_detail",
        "validation_errors",
        "warnings",
    )

    def __init__(self, investigation_id: str) -> None:
        """Initialize an empty record for ``investigation_id``."""
        self.id = investigation_id
        self.status = "queued"
        self.runtime: str | None = None
        self.summary: str | None = None
        #: The full DeterministicSummary, so the report does not have to
        #: reconstruct counts it already computed.
        self.summary_detail: Any = None
        self.model_provider: str | None = None
        self.model_name: str | None = None
        self.error_group_count = 0
        self.event_count = 0
        self.warnings: list[str] = []
        self.created_at = None
        self.completed_at: str | None = None
        self.duration_seconds: float | None = None
        self.processing_steps: list[dict[str, Any]] = []
        self.report: dict[str, Any] | None = None
        self.error_groups: list[ErrorGroup] = []
        self.evidence: list[EvidenceItem] = []
        self.findings: list[dict[str, Any]] = []
        self.ai_skipped_reason: str | None = None
        self.validation_errors: list[str] = []
        self.model_response: str | None = None
        self.raw_input: str | None = None
        self.retry_count = 0
        self.current_step: str | None = None
        self.error_message: str | None = None


class InvestigationStore(abc.ABC):
    """Storage contract used by the API layer."""

    @abc.abstractmethod
    def create(self, record: InvestigationRecord) -> None:
        """Insert a new record."""

    @abc.abstractmethod
    def get(self, investigation_id: str) -> InvestigationRecord | None:
        """Return a record, or None when it does not exist."""

    @abc.abstractmethod
    def update(self, record: InvestigationRecord) -> None:
        """Persist changes to an existing record."""

    @abc.abstractmethod
    def delete(self, investigation_id: str) -> bool:
        """Delete a record and its dependents. Returns True when one was removed."""

    @abc.abstractmethod
    def list(self, limit: int, offset: int) -> tuple[list[InvestigationRecord], int]:
        """Return a page of records (newest first) and the total count."""

    def close(self) -> None:  # noqa: B027 - optional hook, not every store has resources
        """Release resources. Default no-op for stores that hold none."""


class InMemoryStore(InvestigationStore):
    """Process-local store. Suitable for the MVP and for tests."""

    def __init__(self) -> None:
        """Initialize an empty store."""
        self._records: dict[str, InvestigationRecord] = {}

    def create(self, record: InvestigationRecord) -> None:
        """Insert a new record."""
        self._records[record.id] = record

    def get(self, investigation_id: str) -> InvestigationRecord | None:
        """Return a record, or None."""
        return self._records.get(investigation_id)

    def update(self, record: InvestigationRecord) -> None:
        """Persist changes (the record is mutated in place)."""
        self._records[record.id] = record

    def delete(self, investigation_id: str) -> bool:
        """Delete a record."""
        return self._records.pop(investigation_id, None) is not None

    def list(self, limit: int, offset: int) -> tuple[list[InvestigationRecord], int]:
        """Return a page of records, newest first."""
        ordered = sorted(
            self._records.values(),
            key=lambda r: r.created_at or "",
            reverse=True,
        )
        return ordered[offset : offset + limit], len(ordered)

    def clear(self) -> None:
        """Remove everything. Used by tests."""
        self._records.clear()
