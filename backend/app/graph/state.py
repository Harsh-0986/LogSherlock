"""LangGraph investigation state (SPEC §8.2)."""

from __future__ import annotations

from typing import Any, TypedDict

from app.config import Settings
from app.logengine.models import (
    DeterministicSummary,
    ErrorGroup,
    EvidenceBundle,
    LogEvent,
)
from app.logengine.pipeline import ParsedStage


class ProcessingStep(TypedDict):
    """One observable graph-node transition (SPEC §8.4)."""

    step: str
    status: str
    started_at: str
    completed_at: str | None
    summary: str | None


class InvestigationState(TypedDict, total=False):
    """Typed state threaded through the investigation graph."""

    # --- Identity & configuration ---
    investigation_id: str
    settings: Settings

    # --- Input ---
    raw_input: str
    context: str | None
    requested_runtime: str

    # --- Deterministic outputs ---
    #: Internal payload from parse_logs, consumed by rank_errors.
    parsed_stage: ParsedStage
    summary: DeterministicSummary
    events: list[LogEvent]
    groups: list[ErrorGroup]
    evidence: EvidenceBundle

    # --- AI outputs ---
    model_response: str | None
    # Decoded model JSON, carried between investigate/validate/repair nodes.
    # (LangGraph reserves underscore-prefixed keys, so these must not use one.)
    decoded_report: dict[str, Any] | None
    decode_error: str | None
    report: dict[str, Any] | None
    findings: list[dict[str, Any]]
    validation_errors: list[str]
    repair_attempted: bool
    #: Raw per-group model responses, before validation.
    raw_findings: list[dict[str, Any]]
    #: Fingerprints of groups whose finding was rejected, for one bounded retry.
    rejected_fingerprints: list[str]
    #: Deterministically derived report summary (no model call involved).
    model_summary: str
    #: Per-group call failures, surfaced as report warnings.
    model_call_failures: list[str]

    # --- Observability ---
    processing_steps: list[ProcessingStep]
    warnings: list[str]
    started_at: str
    completed_at: str | None
    duration_seconds: float | None
    status: str
    error_message: str | None
    model_metadata: dict[str, Any] | None
    # True when the AI stage was skipped or failed but the report is still useful.
    ai_skipped_reason: str | None


TERMINAL_STATUSES = frozenset({"completed", "partial", "failed"})
