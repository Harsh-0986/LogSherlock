"""SQLAlchemy models (SPEC §11).

Four tables: investigations, error_groups, findings, processing_steps. Every
child row is keyed to an investigation and cascades on delete, so removing an
investigation removes everything attached to it (SPEC §11.5).

Raw log content is deliberately absent from every table. It is only written when
``PERSIST_RAW_LOGS`` is enabled, and even then never into these models.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all models."""


def _utcnow() -> datetime:
    """Timezone-aware current time."""
    return datetime.now(UTC)


class Investigation(Base):
    """One investigation and its headline counts (SPEC §11.1)."""

    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    runtime: Mapped[str | None] = mapped_column(String(32))

    summary: Mapped[str | None] = mapped_column(Text)
    summary_detail: Mapped[str | None] = mapped_column(Text)

    model_provider: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(128))

    error_group_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    duration_seconds: Mapped[float | None] = mapped_column(Float)

    # Safe processing warnings only. Never raw log content.
    warnings_json: Mapped[str | None] = mapped_column(Text)
    validation_errors_json: Mapped[str | None] = mapped_column(Text)
    ai_skipped_reason: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    #: Graph node currently executing, so the UI can show live progress.
    current_step: Mapped[str | None] = mapped_column(String(64))

    #: The redacted evidence bundle, as JSON. Stored so a saved report can be
    #: reconstructed and its citations re-verified without the original log.
    evidence_json: Mapped[str | None] = mapped_column(Text)

    #: True only when the model produced findings. Drives the honest banner.
    ai_analysis_performed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: Raw input is stored here ONLY when PERSIST_RAW_LOGS is explicitly enabled.
    raw_input: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    error_groups: Mapped[list[ErrorGroupRow]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    findings: Mapped[list[FindingRow]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="FindingRow.id",
    )
    processing_steps: Mapped[list[ProcessingStepRow]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_investigations_created_at", "created_at"),
        Index("ix_investigations_status", "status"),
    )


class ErrorGroupRow(Base):
    """A persisted error group (SPEC §11.2)."""

    __tablename__ = "error_groups"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )

    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    exception_type: Mapped[str | None] = mapped_column(String(255))
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    severity_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Redacted representative event and the evidence citations for this group.
    representative_event_json: Mapped[str | None] = mapped_column(Text)
    representative_stack: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[str | None] = mapped_column(Text)
    services_json: Mapped[str | None] = mapped_column(Text)
    line_numbers_json: Mapped[str | None] = mapped_column(Text)

    investigation: Mapped[Investigation] = relationship(back_populates="error_groups")

    __table_args__ = (
        Index("ix_error_groups_investigation_id", "investigation_id"),
        Index("ix_error_groups_fingerprint", "fingerprint"),
    )


class FindingRow(Base):
    """A persisted finding (SPEC §11.3)."""

    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    error_group_id: Mapped[str | None] = mapped_column(String(64))

    title: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    likely_cause: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    reasoning_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")

    #: "model" or "deterministic". The report never implies the model spoke
    #: when it did not.
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="model")

    evidence_ids_json: Mapped[str | None] = mapped_column(Text)
    alternatives_json: Mapped[str | None] = mapped_column(Text)
    missing_information_json: Mapped[str | None] = mapped_column(Text)
    steps_json: Mapped[str | None] = mapped_column(Text)

    investigation: Mapped[Investigation] = relationship(back_populates="findings")

    __table_args__ = (Index("ix_findings_investigation_id", "investigation_id"),)


class ProcessingStepRow(Base):
    """A persisted graph-node transition (SPEC §11.4)."""

    __tablename__ = "processing_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )

    #: Graph node name, e.g. "parse_logs".
    step_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed")
    #: Safe output summary. Never raw log content.
    summary: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    investigation: Mapped[Investigation] = relationship(back_populates="processing_steps")

    __table_args__ = (Index("ix_processing_steps_investigation_id", "investigation_id"),)
