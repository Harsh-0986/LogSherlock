"""SQLAlchemy-backed :class:`~app.store.base.InvestigationStore` (SPEC §11).

Converts between the in-memory record the job manager works with and the
relational schema. Final persistence happens in one transaction (SPEC §11.5).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.errors import PersistenceError
from app.db.models import ErrorGroupRow, FindingRow, Investigation, ProcessingStepRow
from app.db.session import get_session_factory
from app.logengine.models import ErrorGroup, EvidenceItem, LogLevel, Severity
from app.store.base import InvestigationRecord, InvestigationStore

logger = logging.getLogger("logsherlock.store")


def _dumps(value: Any) -> str | None:
    """Serialise to JSON, returning None for empty values."""
    if value is None:
        return None
    return json.dumps(value, default=str)


def _loads(raw: str | None, fallback: Any) -> Any:
    """Deserialise JSON, tolerating corrupt or legacy rows."""
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Discarding malformed JSON column during read")
        return fallback


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO timestamp, tolerating None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class SqlAlchemyStore(InvestigationStore):
    """Persists investigations in SQLite."""

    def __init__(self, database_url: str) -> None:
        """Open a store against ``database_url``, creating tables if needed."""
        self._database_url = database_url
        self._factory = get_session_factory(database_url)

        from app.db.session import create_all

        create_all(database_url)

    # -- writes -----------------------------------------------------------

    def create(self, record: InvestigationRecord) -> None:
        """Insert a new investigation row."""
        session = self._factory()
        try:
            session.add(self._to_row(record, existing=None, session=session))
            session.commit()
        except Exception as exc:
            session.rollback()
            raise PersistenceError(f"Could not create investigation: {exc}") from exc
        finally:
            session.close()

    def update(self, record: InvestigationRecord) -> None:
        """Replace an investigation and its children in one transaction."""
        session = self._factory()
        try:
            row = session.get(Investigation, record.id)
            if row is None:
                session.add(self._to_row(record, existing=None, session=session))
            else:
                # Children are rewritten wholesale: they are a projection of the
                # run, not independently edited.
                for child in (row.error_groups, row.findings, row.processing_steps):
                    child.clear()
                session.flush()
                self._to_row(record, existing=row, session=session)
            session.commit()
        except Exception as exc:
            session.rollback()
            raise PersistenceError(f"Could not save investigation: {exc}") from exc
        finally:
            session.close()

    def delete(self, investigation_id: str) -> bool:
        """Delete an investigation; children cascade."""
        session = self._factory()
        try:
            row = session.get(Investigation, investigation_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True
        except Exception as exc:
            session.rollback()
            raise PersistenceError(f"Could not delete investigation: {exc}") from exc
        finally:
            session.close()

    # -- reads ------------------------------------------------------------

    def get(self, investigation_id: str) -> InvestigationRecord | None:
        """Return a fully hydrated record."""
        session = self._factory()
        try:
            row = session.get(Investigation, investigation_id)
            return self._from_row(row) if row is not None else None
        finally:
            session.close()

    def list(self, limit: int, offset: int) -> tuple[list[InvestigationRecord], int]:
        """Return a page of records, newest first, plus the total count."""
        session = self._factory()
        try:
            total = session.scalar(select(func.count()).select_from(Investigation)) or 0
            rows = session.scalars(
                select(Investigation)
                .order_by(Investigation.created_at.desc(), Investigation.id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [self._from_row(row) for row in rows], int(total)
        finally:
            session.close()

    def close(self) -> None:
        """No pooled resources to release beyond the shared session factory."""
        return None

    # -- mapping ----------------------------------------------------------

    def _to_row(
        self,
        record: InvestigationRecord,
        *,
        existing: Investigation | None,
        session: Session,
    ) -> Investigation:
        """Build or update the ORM row for ``record``."""
        row = existing or Investigation(id=record.id)

        row.status = record.status
        row.runtime = record.runtime
        row.summary = record.summary
        row.summary_detail = _dumps(record.summary_detail)
        row.model_provider = record.model_provider
        row.model_name = record.model_name
        row.error_group_count = record.error_group_count
        row.event_count = record.event_count
        row.duration_seconds = record.duration_seconds
        row.warnings_json = _dumps(record.warnings or [])
        row.validation_errors_json = _dumps(record.validation_errors or [])
        row.ai_skipped_reason = record.ai_skipped_reason
        row.error_message = record.error_message
        row.evidence_json = _dumps([e.model_dump(mode="json") for e in record.evidence])
        row.ai_analysis_performed = bool(record.model_response and record.findings)

        created = _parse_dt(record.created_at) or datetime.now().astimezone()
        row.created_at = created
        row.completed_at = _parse_dt(record.completed_at)

        if existing is None:
            row.raw_input = record.raw_input
            session.add(row)

        for step in record.processing_steps:
            session.add(
                ProcessingStepRow(
                    investigation_id=record.id,
                    step_name=step.get("step", "unknown"),
                    status=step.get("status", "completed"),
                    summary=step.get("summary"),
                    started_at=_parse_dt(step.get("started_at")) or created,
                    completed_at=_parse_dt(step.get("completed_at")),
                )
            )

        group_ids: dict[str, str] = {}
        for group in record.error_groups:
            group_ids[group.fingerprint] = group.id
            session.add(self._group_to_row(record.id, group))

        for index, finding in enumerate(record.findings):
            session.add(self._finding_to_row(record.id, finding, group_ids, index))

        return row

    @staticmethod
    def _group_to_row(investigation_id: str, group: ErrorGroup) -> ErrorGroupRow:
        """Map an ErrorGroup to its row."""
        return ErrorGroupRow(
            id=group.id,
            investigation_id=investigation_id,
            fingerprint=group.fingerprint,
            title=group.title,
            exception_type=group.exception_type,
            severity=group.severity.value,
            severity_reason=group.severity_reason,
            count=group.count,
            first_seen=group.first_seen,
            last_seen=group.last_seen,
            # Redacted representative event only — never the raw line.
            representative_event_json=_dumps(group.representative_event.model_dump(mode="json")),
            representative_stack=group.representative_stack,
            evidence_json=_dumps(list(group.evidence_ids)),
            services_json=_dumps(list(group.services)),
            line_numbers_json=_dumps(list(group.line_numbers)),
        )

    @staticmethod
    def _finding_to_row(
        investigation_id: str,
        finding: dict[str, Any],
        group_ids: dict[str, str],
        index: int,
    ) -> FindingRow:
        """Map a finding dict to its row.

        ``index`` guarantees a unique primary key when the caller did not
        assign an id — findings are a primary key, so a collision would abort
        the whole transaction.
        """
        return FindingRow(
            id=finding.get("id") or f"fnd_{investigation_id}_{index:03d}",
            investigation_id=investigation_id,
            error_group_id=group_ids.get(finding.get("fingerprint", "")),
            title=finding.get("title", "Untitled finding"),
            fingerprint=finding.get("fingerprint", ""),
            severity=finding.get("severity", "medium"),
            likely_cause=finding.get("likely_cause", ""),
            confidence=finding.get("confidence", "low"),
            reasoning_summary=finding.get("reasoning_summary", ""),
            source=finding.get("source", "model"),
            evidence_ids_json=_dumps(finding.get("evidence_ids", [])),
            alternatives_json=_dumps(finding.get("alternative_hypotheses", [])),
            missing_information_json=_dumps(finding.get("missing_information", [])),
            steps_json=_dumps(finding.get("recommended_steps", [])),
        )

    @staticmethod
    def _from_row(row: Investigation) -> InvestigationRecord:
        """Rebuild an InvestigationRecord from persisted rows."""
        record = InvestigationRecord(row.id)
        record.status = row.status
        record.runtime = row.runtime
        record.summary = row.summary
        record.summary_detail = _loads(row.summary_detail, None)
        record.model_provider = row.model_provider
        record.model_name = row.model_name
        record.error_group_count = row.error_group_count
        record.event_count = row.event_count
        record.duration_seconds = row.duration_seconds
        record.warnings = _loads(row.warnings_json, [])
        record.validation_errors = _loads(row.validation_errors_json, [])
        record.ai_skipped_reason = row.ai_skipped_reason
        record.error_message = row.error_message
        record.raw_input = row.raw_input
        record.created_at = row.created_at.isoformat() if row.created_at else None
        record.completed_at = row.completed_at.isoformat() if row.completed_at else None

        evidence_payload = _loads(row.evidence_json, [])
        record.evidence = [
            EvidenceItem(
                id=item.get("id", ""),
                group_id=item.get("group_id"),
                line_number=item.get("line_number", 0),
                text=item.get("text", ""),
                kind=item.get("kind", "event"),
                level=LogLevel(item.get("level", "UNKNOWN")),
            )
            for item in evidence_payload
        ]

        record.error_groups = [_group_from_row(g) for g in row.error_groups]
        record.findings = [_finding_from_row(f) for f in row.findings]
        record.processing_steps = [
            {
                "step": s.step_name,
                "status": s.status,
                "started_at": s.started_at.isoformat() if s.started_at else "",
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                "summary": s.summary,
            }
            for s in row.processing_steps
        ]

        return record

    def purge(self) -> None:
        """Remove every row. Used by tests."""
        session = self._factory()
        try:
            for model in (ProcessingStepRow, FindingRow, ErrorGroupRow, Investigation):
                session.execute(delete(model))
            session.commit()
        finally:
            session.close()


def _group_from_row(row: ErrorGroupRow) -> ErrorGroup:
    """Rebuild an ErrorGroup, with a minimal representative event.

    The full parsed event is not restored — only what the report renders.
    """
    from app.logengine.models import LogEvent

    representative_payload = _loads(row.representative_event_json, {}) or {}
    representative = LogEvent.model_validate(
        {
            **representative_payload,
            "message": representative_payload.get("message", row.title),
            "raw_text": representative_payload.get("raw_text", row.title),
            "line_number": representative_payload.get("line_number", 1),
        }
    )

    return ErrorGroup(
        id=row.id,
        fingerprint=row.fingerprint,
        title=row.title,
        exception_type=row.exception_type,
        severity=Severity(row.severity),
        count=row.count,
        first_seen=row.first_seen,
        last_seen=row.last_seen,
        severity_reason=row.severity_reason,
        services=tuple(_loads(row.services_json, [])),
        line_numbers=tuple(_loads(row.line_numbers_json, [])),
        evidence_ids=tuple(_loads(row.evidence_json, [])),
        representative_event=representative,
        representative_stack=row.representative_stack,
    )


def _finding_from_row(row: FindingRow) -> dict[str, Any]:
    """Rebuild a finding dict."""
    return {
        "id": row.id,
        "fingerprint": row.fingerprint,
        "title": row.title,
        "severity": row.severity,
        "likely_cause": row.likely_cause,
        "confidence": row.confidence,
        "reasoning_summary": row.reasoning_summary,
        "source": row.source,
        "evidence_ids": _loads(row.evidence_ids_json, []),
        "alternative_hypotheses": _loads(row.alternatives_json, []),
        "missing_information": _loads(row.missing_information_json, []),
        "recommended_steps": _loads(row.steps_json, []),
    }
