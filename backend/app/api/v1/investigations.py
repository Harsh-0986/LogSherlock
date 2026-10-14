"""Investigation endpoints (SPEC §10.3 - §10.8)."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse

from app.api.schemas import (
    SUPPORTED_UPLOAD_EXTENSIONS,
    DeterministicSummaryOut,
    ErrorGroupOut,
    EvidenceOut,
    FindingOut,
    InvestigationListItem,
    InvestigationListResponse,
    InvestigationReportResponse,
    InvestigationStatusResponse,
    ProcessingStepOut,
    RecommendedStepOut,
    StartInvestigationRequest,
    StartInvestigationResponse,
)
from app.config import Settings, get_settings
from app.core.errors import (
    InvalidInputError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedFileTypeError,
)
from app.core.ids import new_finding_id
from app.jobs.manager import JobManager
from app.store.base import InvestigationRecord

logger = logging.getLogger("logsherlock.api")

router = APIRouter(prefix="/investigations", tags=["investigations"])


def get_job_manager(request: Request) -> JobManager:
    """Return the shared job manager from app state."""
    return request.app.state.job_manager


JobManagerDep = Annotated[JobManager, Depends(get_job_manager)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=StartInvestigationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_investigation(
    body: StartInvestigationRequest,
    manager: JobManagerDep,
) -> StartInvestigationResponse:
    """Accept pasted log content and start an investigation.

    Declared ``async`` so it runs on the event loop; ``submit`` creates the
    background task and cannot do so from a worker thread.
    """
    record = manager.submit(
        content=body.content,
        runtime=body.runtime,
        context=body.context,
        filename=body.filename,
    )
    return StartInvestigationResponse(id=record.id, status=record.status)


@router.post(
    "/upload",
    response_model=StartInvestigationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_investigation(
    manager: JobManagerDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="A .log, .txt, or .jsonl file.")],
    runtime: Annotated[str, Form()] = "auto",
    context: Annotated[str | None, Form()] = None,
) -> StartInvestigationResponse:
    """Accept an uploaded log file and start an investigation.

    Size is checked from the declared length before the body is fully read, and
    again while reading, so an oversized upload cannot exhaust memory
    (SPEC §12.4).
    """
    _check_extension(file)

    max_bytes = settings.max_upload_bytes
    if file.size is not None and file.size > max_bytes:
        raise PayloadTooLargeError(
            f"File is {file.size} bytes, above the {settings.max_upload_mb} MB limit.",
            details={"max_bytes": max_bytes},
        )

    content = await _read_limited(file, max_bytes, settings.max_line_length)

    record = manager.submit(
        content=content,
        runtime=runtime,
        context=context,
        filename=file.filename,
    )
    return StartInvestigationResponse(id=record.id, status=record.status)


def _check_extension(file: UploadFile) -> None:
    """Validate the filename extension. Filenames are untrusted (SPEC §12.4)."""
    name = (file.filename or "").strip()
    # Never use the name as a path; only the extension matters.
    suffix = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if suffix not in SUPPORTED_UPLOAD_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"Unsupported file type '{suffix or 'unknown'}'. "
            f"Accepted: {', '.join(sorted(SUPPORTED_UPLOAD_EXTENSIONS))}",
            details={"accepted": sorted(SUPPORTED_UPLOAD_EXTENSIONS)},
        )


async def _read_limited(file: UploadFile, max_bytes: int, max_line_length: int) -> str:
    """Read an upload with a hard byte ceiling and safe decoding."""
    chunks: list[bytes] = []
    total = 0

    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLargeError(
                f"Upload exceeded the {max_bytes // (1024 * 1024)} MB limit."
            )
        chunks.append(chunk)

    raw = b"".join(chunks)
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        # Try a common fallback rather than failing outright (SPEC §12.4).
        content = raw.decode("utf-8", errors="replace")

    if not content.strip():
        raise InvalidInputError("The uploaded file is empty.")

    # A single pathological line is rejected rather than truncated silently.
    longest = max((len(line) for line in content.splitlines()), default=0)
    if longest > max_line_length:
        raise PayloadTooLargeError(
            f"A single line is {longest} characters, above the {max_line_length} character limit."
        )

    return content


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@router.get("", response_model=InvestigationListResponse)
def list_investigations(
    manager: JobManagerDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> InvestigationListResponse:
    """Return a paginated list of saved investigations (SPEC §10.7)."""
    records, total = manager.list(limit, offset)
    return InvestigationListResponse(
        items=[_to_list_item(r) for r in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{investigation_id}/status", response_model=InvestigationStatusResponse)
def get_status(
    investigation_id: str,
    manager: JobManagerDep,
) -> InvestigationStatusResponse:
    """Return real processing progress (SPEC §10.5)."""
    record = manager.get(investigation_id)
    return InvestigationStatusResponse(
        id=record.id,
        status=record.status,  # type: ignore[arg-type]
        current_step=record.current_step,
        steps=[ProcessingStepOut(**step) for step in record.processing_steps],
        error_message=record.error_message,
    )


@router.get("/{investigation_id}", response_model=InvestigationReportResponse)
def get_report(
    investigation_id: str,
    manager: JobManagerDep,
    settings: SettingsDep,
) -> InvestigationReportResponse:
    """Return the full investigation report (SPEC §10.6).

    Raw logs are not returned unless persistence was explicitly enabled.
    """
    record = manager.get(investigation_id)
    return _to_report(record, settings)


@router.post("/{investigation_id}/retry-ai", response_model=StartInvestigationResponse)
async def retry_ai_analysis(
    investigation_id: str,
    manager: JobManagerDep,
) -> StartInvestigationResponse:
    """Re-run the AI stage for an existing investigation.

    Only the model stage is repeated: the stored groups and evidence are reused,
    and only groups the previous run did not explain are attempted, so a retry
    costs a fraction of the original and cannot repeat work that already
    succeeded. The previous report is preserved if the retry yields nothing.
    """
    record = manager.retry_ai(investigation_id)
    return StartInvestigationResponse(id=record.id, status="running")


@router.delete("/{investigation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_investigation(
    investigation_id: str,
    manager: JobManagerDep,
) -> JSONResponse:
    """Delete an investigation and its dependent records (SPEC §10.8)."""
    manager.delete(investigation_id)
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def _to_list_item(record: InvestigationRecord) -> InvestigationListItem:
    """Map a record to a list row."""
    return InvestigationListItem(
        id=record.id,
        status=record.status,  # type: ignore[arg-type]
        runtime=record.runtime,
        summary=record.summary,
        error_group_count=record.error_group_count,
        event_count=record.event_count,
        model_provider=record.model_provider,
        model_name=record.model_name,
        created_at=record.created_at or "",
        completed_at=record.completed_at,
    )


def _to_report(record: InvestigationRecord, settings: Settings) -> InvestigationReportResponse:
    """Map a record to the full report response."""
    return InvestigationReportResponse(
        id=record.id,
        status=record.status,  # type: ignore[arg-type]
        created_at=record.created_at or "",
        completed_at=record.completed_at,
        duration_seconds=record.duration_seconds,
        summary=_to_summary(record),
        error_groups=[
            ErrorGroupOut(
                id=g.id,
                fingerprint=g.fingerprint,
                title=g.title,
                exception_type=g.exception_type,
                severity=g.severity,
                severity_reason=g.severity_reason,
                count=g.count,
                first_seen=g.first_seen.isoformat() if g.first_seen else None,
                last_seen=g.last_seen.isoformat() if g.last_seen else None,
                services=list(g.services),
                line_numbers=list(g.line_numbers),
                representative_stack=g.representative_stack,
            )
            for g in record.error_groups
        ],
        findings=[_to_finding(f) for f in record.findings],
        evidence=[
            EvidenceOut(
                id=e.id,
                group_id=e.group_id,
                line_number=e.line_number,
                text=e.text,
                kind=e.kind,
                level=e.level,
            )
            for e in record.evidence
        ],
        processing_steps=[ProcessingStepOut(**s) for s in record.processing_steps],
        warnings=list(record.warnings),
        validation_errors=list(record.validation_errors),
        error_message=record.error_message,
        model_provider=record.model_provider,
        model_name=record.model_name,
        ai_skipped_reason=record.ai_skipped_reason,
        ai_analysis_performed=any(
            f.get("source", "model") == "model" for f in record.findings
        ),
        retry_count=record.retry_count,
        can_retry=(
            record.status not in {"running", "queued"}
            and bool(record.error_groups)
        ),
        raw_input_included=bool(settings.persist_raw_logs and record.raw_input),
        raw_input=record.raw_input if settings.persist_raw_logs else None,
    )


def _to_summary(record: InvestigationRecord) -> DeterministicSummaryOut | None:
    """Return the deterministic summary computed during the run."""
    detail = record.summary_detail
    if not detail:
        return None
    return DeterministicSummaryOut.model_validate(detail)


def _to_finding(finding: dict[str, Any]) -> FindingOut:
    """Map an internal finding dict to the response model."""
    return FindingOut(
        id=finding.get("id") or new_finding_id(),
        fingerprint=finding.get("fingerprint", ""),
        title=finding.get("title", "Untitled finding"),
        severity=finding.get("severity", "medium"),
        likely_cause=finding.get("likely_cause", ""),
        confidence=finding.get("confidence", "low"),
        evidence_ids=finding.get("evidence_ids", []),
        reasoning_summary=finding.get("reasoning_summary", ""),
        alternative_hypotheses=finding.get("alternative_hypotheses", []),
        missing_information=finding.get("missing_information", []),
        recommended_steps=[
            RecommendedStepOut(
                action=s.get("action", ""),
                reason=s.get("reason", ""),
                verification=s.get("verification", ""),
            )
            for s in finding.get("recommended_steps", [])
        ],
        source=finding.get("source", "model"),
    )


__all__ = ["NotFoundError", "router"]
