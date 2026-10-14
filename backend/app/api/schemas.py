"""Request and response models for the investigation API (SPEC §10).

Every response shape is declared here so the contract is explicit and the
frontend can be typed against it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.logengine.models import LogLevel, Severity

# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

SUPPORTED_UPLOAD_EXTENSIONS = {".log", ".txt", ".jsonl"}


class StartInvestigationRequest(BaseModel):
    """Body for POST /api/v1/investigations."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, description="Raw log content to analyse.")
    filename: str | None = None
    runtime: str = "auto"
    context: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional operator context, treated as untrusted data.",
    )

    @field_validator("runtime")
    @classmethod
    def _known_runtime(cls, value: str) -> str:
        allowed = {"auto", "python", "nodejs", "dotnet", "jsonl", "generic"}
        if value not in allowed:
            raise ValueError(f"runtime must be one of {sorted(allowed)}")
        return value

    @field_validator("context")
    @classmethod
    def _strip_context(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class StartInvestigationResponse(BaseModel):
    """Body returned when an investigation is accepted."""

    id: str
    status: str


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

InvestigationStatus = Literal["queued", "running", "completed", "partial", "failed"]


class ProcessingStepOut(BaseModel):
    """One observable graph-node transition (SPEC §8.4)."""

    step: str
    status: str
    started_at: str
    completed_at: str | None = None
    summary: str | None = None


class InvestigationStatusResponse(BaseModel):
    """Body for GET /api/v1/investigations/{id}/status."""

    id: str
    status: InvestigationStatus
    current_step: str | None = None
    steps: list[ProcessingStepOut] = Field(default_factory=list)
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


class DeterministicSummaryOut(BaseModel):
    """Counts derived purely from parsing and grouping."""

    total_lines: int
    total_events: int
    error_group_count: int
    warning_group_count: int
    runtime: str
    detected_runtime: str


class ErrorGroupOut(BaseModel):
    """One prioritized error group."""

    id: str
    fingerprint: str
    title: str
    exception_type: str | None = None
    severity: Severity
    severity_reason: str
    count: int
    first_seen: str | None = None
    last_seen: str | None = None
    services: list[str] = Field(default_factory=list)
    line_numbers: list[int] = Field(default_factory=list)
    representative_stack: str | None = None


class RecommendedStepOut(BaseModel):
    """One recommended debugging action."""

    action: str
    reason: str = ""
    verification: str = ""


class FindingOut(BaseModel):
    """One validated AI finding."""

    id: str
    fingerprint: str
    title: str
    severity: Severity
    likely_cause: str
    confidence: Literal["low", "medium", "high"]
    evidence_ids: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""
    alternative_hypotheses: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    recommended_steps: list[RecommendedStepOut] = Field(default_factory=list)
    source: Literal["model", "deterministic"] = "model"


class EvidenceOut(BaseModel):
    """One cited evidence line."""

    id: str
    group_id: str | None = None
    line_number: int
    text: str
    kind: str
    level: LogLevel


class InvestigationListItem(BaseModel):
    """One row in the paginated list endpoint."""

    id: str
    status: InvestigationStatus
    runtime: str | None = None
    summary: str | None = None
    error_group_count: int = 0
    event_count: int = 0
    model_provider: str | None = None
    model_name: str | None = None
    created_at: str
    completed_at: str | None = None


class InvestigationListResponse(BaseModel):
    """Paginated list of saved investigations."""

    items: list[InvestigationListItem] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class InvestigationReportResponse(BaseModel):
    """Full report for GET /api/v1/investigations/{id} (SPEC §10.6)."""

    id: str
    status: InvestigationStatus
    created_at: str
    completed_at: str | None = None
    duration_seconds: float | None = None

    summary: DeterministicSummaryOut | None = None
    error_groups: list[ErrorGroupOut] = Field(default_factory=list)
    findings: list[FindingOut] = Field(default_factory=list)
    evidence: list[EvidenceOut] = Field(default_factory=list)

    processing_steps: list[ProcessingStepOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    error_message: str | None = None

    model_provider: str | None = None
    model_name: str | None = None

    ai_skipped_reason: str | None = None
    #: True when the model's own summary is shown; false means it was generated
    #: deterministically because inference was unavailable.
    ai_analysis_performed: bool = False
    #: How many AI retries have run for this investigation.
    retry_count: int = 0
    #: Whether the UI should offer a retry right now.
    can_retry: bool = False

    raw_input_included: bool = False
    raw_input: str | None = None

    model_config = ConfigDict(protected_namespaces=())


def to_jsonable(value: Any) -> Any:
    """Best-effort conversion of internal values to JSON-safe primitives."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
