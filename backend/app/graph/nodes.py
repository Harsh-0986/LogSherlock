"""LangGraph investigation nodes (SPEC §8.3).

Each node returns a partial state update. Parsing and grouping are ordinary
Python calls, never LLM calls. The AI stage is a single node with one bounded
repair attempt and a deterministic fallback (SPEC §8.5).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from app.core.errors import InvalidInputError, PayloadTooLargeError
from app.core.ids import utc_now
from app.graph.state import InvestigationState, ProcessingStep
from app.graph.validation import validate_report
from app.logengine.models import DeterministicSummary, Runtime, Severity
from app.logengine.pipeline import (
    ParsedStage,
    build_redactor,
    count_lines,
    redact_events,
    stage_evidence,
    stage_group,
    stage_parse,
    stage_rank,
)
from app.models.base import (
    BaseChatModel,
    ChatMessage,
    ModelError,
    ModelTimeoutError,
    ModelUnavailableError,
)
from app.models.prompts import SYSTEM_PROMPT, build_repair_prompt, build_user_prompt

logger = logging.getLogger("logsherlock.graph")


# ---------------------------------------------------------------------------
# Execution tracking (SPEC §8.4)
# ---------------------------------------------------------------------------


def _record(
    state: InvestigationState,
    name: str,
    status: str,
    started_at: str,
    summary: str | None,
) -> list[ProcessingStep]:
    """Return the updated step list with one entry appended."""
    steps = list(state.get("processing_steps", []))
    steps.append(
        ProcessingStep(
            step=name,
            status=status,
            started_at=started_at,
            completed_at=utc_now().isoformat(),
            summary=summary,
        )
    )
    return steps


def _started(state: InvestigationState) -> str:
    """Timestamp for the current node."""
    return state.get("started_at") or utc_now().isoformat()


# ---------------------------------------------------------------------------
# 1. validate_input
# ---------------------------------------------------------------------------


def validate_input(state: InvestigationState) -> dict[str, Any]:
    """Check size and format limits, reject empty content (SPEC §8.3)."""
    started = _started(state)
    settings = state["settings"]
    raw = state.get("raw_input") or ""

    if not raw.strip():
        raise InvalidInputError("The supplied log content is empty.")

    max_bytes = settings.max_upload_bytes
    if len(raw.encode("utf-8")) > max_bytes:
        raise PayloadTooLargeError(
            f"Log content exceeds the {settings.max_upload_mb} MB limit.",
            details={"max_bytes": max_bytes},
        )

    line_count = count_lines(raw)
    if line_count > settings.max_log_lines:
        raise PayloadTooLargeError(
            f"Log content has {line_count} lines, above the {settings.max_log_lines} line limit.",
            details={"max_lines": settings.max_log_lines},
        )

    longest = max((len(line) for line in raw.splitlines()), default=0)
    if longest > settings.max_line_length:
        raise PayloadTooLargeError(
            f"A single log line is {longest} characters, above the "
            f"{settings.max_line_length} character limit.",
            details={"max_line_length": settings.max_line_length},
        )

    return {
        "processing_steps": _record(
            state,
            "validate_input",
            "completed",
            started,
            f"Accepted {line_count} lines ({len(raw)} chars)",
        ),
        "status": "running",
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# 2-5. parse_logs / normalize_and_group / rank_errors / select_evidence
# ---------------------------------------------------------------------------


def parse_logs(state: InvestigationState) -> dict[str, Any]:
    """Detect the format, parse, and redact sensitive values."""
    started = _started(state)
    settings = state["settings"]
    raw = state.get("raw_input") or ""

    requested = Runtime(state.get("requested_runtime") or "auto")
    parsed = stage_parse(text=raw, runtime=requested)
    events, redaction_count = redact_events(parsed.events, build_redactor(settings))

    warnings = list(parsed.warnings)
    if redaction_count:
        warnings.append(
            f"Redacted {redaction_count} potentially sensitive value(s) before analysis. "
            "Pattern-based redaction is best-effort and cannot detect every secret."
        )

    return {
        "processing_steps": _record(
            state,
            "parse_logs",
            "completed",
            started,
            f"Parsed {len(events)} events ({parsed.detected_runtime.value} format); "
            f"redacted {redaction_count} value(s)",
        ),
        "events": events,
        "warnings": warnings,
        "parsed_stage": parsed,
    }


def normalize_and_group(state: InvestigationState) -> dict[str, Any]:
    """Normalize variable values so recurring errors group together."""
    started = _started(state)
    events = stage_group(state.get("events") or [])
    return {
        "processing_steps": _record(
            state,
            "normalize_and_group",
            "completed",
            started,
            f"Normalized {len(events)} event(s)",
        ),
        "events": events,
    }


def rank_errors(state: InvestigationState) -> dict[str, Any]:
    """Assign deterministic severity and rank the groups."""
    started = _started(state)
    groups = stage_rank(state.get("events") or [])
    events = state.get("events") or []

    error_count = sum(
        1 for g in groups if g.severity in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}
    )
    warning_count = sum(1 for g in groups if g.severity is Severity.LOW)

    parsed: ParsedStage | None = state.get("parsed_stage")
    summary = DeterministicSummary(
        total_lines=parsed.total_lines if parsed else 0,
        total_events=len(events),
        error_group_count=error_count,
        warning_group_count=warning_count,
        runtime=Runtime(state.get("requested_runtime") or "auto"),
        detected_runtime=parsed.detected_runtime if parsed else Runtime.GENERIC,
    )

    return {
        "processing_steps": _record(
            state,
            "rank_errors",
            "completed",
            started,
            f"Ranked {len(groups)} group(s): {error_count} error, {warning_count} warning",
        ),
        "groups": groups,
        "summary": summary,
    }


def select_evidence(state: InvestigationState) -> dict[str, Any]:
    """Select a bounded, redacted evidence set for the model (SPEC §7.8)."""
    started = _started(state)
    evidence = stage_evidence(
        events=state.get("events") or [],
        groups=state.get("groups") or [],
        redactor=build_redactor(state["settings"]),
        settings=state["settings"],
    )

    warnings = list(state.get("warnings") or [])
    if evidence.truncated and evidence.note:
        warnings.append(evidence.note)

    note = "truncated" if evidence.truncated else "complete"
    return {
        "processing_steps": _record(
            state,
            "select_evidence",
            "completed",
            started,
            f"Selected {len(evidence.items)} evidence items ({evidence.total_chars} chars, {note})",
        ),
        "evidence": evidence,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# 6. investigate_with_model
# ---------------------------------------------------------------------------


async def investigate_with_model(
    state: InvestigationState,
    model: BaseChatModel,
) -> dict[str, Any]:
    """Submit a constrained prompt and request structured output.

    Every failure mode here degrades to deterministic analysis rather than
    failing the investigation (SPEC §8.5).
    """
    started = _started(state)
    groups = state.get("groups") or []
    evidence = state.get("evidence")

    base: dict[str, Any] = {
        "validation_errors": [],
        "repair_attempted": False,
    }

    if not groups:
        return {
            **base,
            "processing_steps": _record(
                state,
                "investigate_with_model",
                "skipped",
                started,
                "No error groups found; nothing to investigate",
            ),
            "findings": [],
            "ai_skipped_reason": "No error groups were detected in the input.",
        }

    if evidence is None or not evidence.items:  # pragma: no cover - defensive
        return {
            **base,
            "processing_steps": _record(
                state,
                "investigate_with_model",
                "skipped",
                started,
                "No evidence available to send to the model",
            ),
            "findings": [],
            "ai_skipped_reason": "No evidence could be selected for the model.",
        }

    messages = [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=build_user_prompt(
                groups=groups,
                evidence=evidence,
                context=state.get("context"),
            ),
        ),
    ]

    try:
        response = await model.complete(messages)
    except ModelTimeoutError as exc:
        logger.warning("investigation=%s model timeout", state.get("investigation_id"))
        return {
            **base,
            "processing_steps": _record(
                state,
                "investigate_with_model",
                "failed",
                started,
                f"Model timed out: {exc}",
            ),
            "findings": [],
            "ai_skipped_reason": f"AI analysis timed out: {exc}",
        }
    except (ModelUnavailableError, ModelError) as exc:
        logger.warning("investigation=%s model unavailable", state.get("investigation_id"))
        return {
            **base,
            "processing_steps": _record(
                state,
                "investigate_with_model",
                "failed",
                started,
                f"Model unavailable: {exc}",
            ),
            "findings": [],
            "ai_skipped_reason": f"AI analysis was unavailable: {exc}",
        }

    raw_text = response.text
    parsed, parse_error = _decode(raw_text)

    return {
        **base,
        "processing_steps": _record(
            state,
            "investigate_with_model",
            "completed",
            started,
            f"Model {response.model_name} returned {len(raw_text)} chars "
            f"({'valid JSON' if parsed is not None else 'invalid JSON'})",
        ),
        "model_response": raw_text,
        "model_metadata": {
            "provider": response.provider,
            "model": response.model_name,
        },
        # Carried for the validation node; not part of the public report.
        "decoded_report": parsed,
        "decode_error": parse_error,
    }


def _decode(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Decode a model response into a dict, tolerating code fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return None, f"Response was not valid JSON: {exc}"
    if not isinstance(value, dict):
        return None, "Response JSON was not an object"
    return value, None


# ---------------------------------------------------------------------------
# 7. validate_findings
# ---------------------------------------------------------------------------


def validate_findings(state: InvestigationState) -> dict[str, Any]:
    """Validate the model report against deterministic facts (SPEC §8.3)."""
    started = _started(state)
    groups = state.get("groups") or []
    evidence = state.get("evidence")
    valid_ids = {item.id for item in (evidence.items if evidence else ())}

    decoded = state.get("decoded_report")
    decode_error = state.get("decode_error")

    # The model may never have been reached at all (unavailable, timed out, or
    # there was nothing to investigate). That is a skip, not a decode failure.
    if state.get("model_response") is None:
        reason = state.get("ai_skipped_reason") or "No model output was produced."
        return {
            "processing_steps": _record(
                state,
                "validate_findings",
                "skipped",
                started,
                f"No model output to validate: {reason}",
            ),
            "validation_errors": [],
            "findings": [],
        }

    if decoded is None:
        return {
            "processing_steps": _record(
                state,
                "validate_findings",
                "failed",
                started,
                decode_error or "Model output could not be decoded",
            ),
            "validation_errors": [decode_error or "Model output could not be decoded"],
            "findings": [],
        }

    outcome = validate_report(decoded, groups=groups, valid_evidence_ids=valid_ids)

    summary_text = (
        f"Accepted {len(outcome.findings)} of {len(decoded.get('findings', []))} findings"
    )
    if outcome.discarded:
        summary_text += f"; discarded {len(outcome.discarded)} as invalid"

    return {
        "processing_steps": _record(state, "validate_findings", "completed", started, summary_text),
        "report": decoded,
        "findings": [f.model_dump(mode="json") for f in outcome.findings],
        "validation_errors": outcome.errors,
    }


# ---------------------------------------------------------------------------
# 8. bounded_repair
# ---------------------------------------------------------------------------


async def bounded_repair(state: InvestigationState, model: BaseChatModel) -> dict[str, Any]:
    """Request exactly one constrained correction, then give up (SPEC §8.3).

    Never retries indefinitely. If this fails the caller falls back to the
    deterministic report.
    """
    started = _started(state)
    errors = state.get("validation_errors") or []
    raw = state.get("model_response") or ""

    messages = [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=build_repair_prompt(raw_output=raw, validation_errors=errors),
        ),
    ]

    try:
        response = await model.complete(messages)
    except (ModelUnavailableError, ModelTimeoutError, ModelError) as exc:
        return {
            "processing_steps": _record(
                state,
                "bounded_repair",
                "failed",
                started,
                f"Repair attempt failed: {type(exc).__name__}",
            ),
            "findings": [],
            "repair_attempted": True,
        }

    decoded, decode_error = _decode(response.text)
    if decoded is None:
        return {
            "processing_steps": _record(
                state,
                "bounded_repair",
                "failed",
                started,
                f"Repair output still invalid: {decode_error}",
            ),
            "findings": [],
            "repair_attempted": True,
        }

    return {
        "processing_steps": _record(
            state,
            "bounded_repair",
            "completed",
            started,
            "Model returned a corrected response; re-validating",
        ),
        "repair_attempted": True,
        "decoded_report": decoded,
        "decode_error": None,
    }


# ---------------------------------------------------------------------------
# 9. persist_report
# ---------------------------------------------------------------------------


def persist_report(state: InvestigationState) -> dict[str, Any]:
    """Mark the investigation complete or partial (SPEC §8.3).

    Actual database writes happen in the job manager, in a transaction, after
    this node has decided the final status.
    """
    started = _started(state)
    findings = state.get("findings") or []
    errors = state.get("validation_errors") or []
    skipped = state.get("ai_skipped_reason")

    if skipped:
        status = "partial"
        note = "Completed without AI findings; deterministic analysis only"
    elif errors and not findings:
        status = "partial"
        note = "AI findings failed validation; report shows deterministic analysis only"
    else:
        status = "completed"
        note = f"Saved with {len(findings)} validated findings"

    completed_at = utc_now().isoformat()
    started_dt = _parse_iso(state.get("started_at"))
    duration = _duration_seconds(started_dt)

    return {
        "processing_steps": _record(state, "persist_report", "completed", started, note),
        "status": status,
        "completed_at": completed_at,
        "duration_seconds": duration,
    }


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO timestamp, tolerating None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _duration_seconds(started: datetime | None) -> float | None:
    """Seconds since ``started``, rounded. None when unknown."""
    if started is None:
        return None
    return round((utc_now() - started).total_seconds(), 3)


def build_fallback_findings(groups: list[Any]) -> list[dict[str, Any]]:
    """Build deterministic findings when the model produced nothing usable.

    Used when inference failed or every finding was rejected (SPEC §8.5). These
    are explicitly derived from the deterministic analysis, not from the model,
    so the report stays honest about its origin.
    """
    findings: list[dict[str, Any]] = []
    for group in groups[:8]:
        findings.append(
            {
                "fingerprint": group.fingerprint,
                "title": group.title,
                "severity": group.severity.value,
                "likely_cause": (
                    "Not determined — AI analysis did not produce a usable result. "
                    "Severity is derived deterministically from the logs."
                ),
                "confidence": "low",
                "evidence_ids": list(group.evidence_ids[:4]),
                "reasoning_summary": group.severity_reason,
                "alternative_hypotheses": [],
                "missing_information": [
                    "AI analysis was unavailable for this group; "
                    "verify the cited evidence manually."
                ],
                "recommended_steps": [
                    {
                        "action": (f"Inspect the cited log lines for: {group.title}"),
                        "reason": group.severity_reason,
                        "verification": (
                            "Open the original log at the cited line numbers "
                            "and confirm the pattern."
                        ),
                    }
                ],
                "source": "deterministic",
            }
        )
    return findings


_ = Severity  # re-exported severity for report rendering
