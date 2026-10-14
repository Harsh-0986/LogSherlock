"""LangGraph investigation nodes (SPEC §8.3).

Each node returns a partial state update. Parsing and grouping are ordinary
Python calls, never LLM calls. The AI stage is a single node with one bounded
repair attempt and a deterministic fallback (SPEC §8.5).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime
from typing import Any

from app.core.errors import InvalidInputError, PayloadTooLargeError
from app.core.ids import utc_now
from app.graph.state import InvestigationState, ProcessingStep
from app.graph.validation import validate_findings as validate_findings_batch
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
from app.models.prompts import (
    GROUP_SYSTEM_PROMPT,
    build_group_prompt,
    build_repair_prompt,
    evidence_for_group,
)

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

#: Calls run one at a time with a short pause between them.
#:
#: Firing every request at once is faster but invites provider rate limits, and
#: a run that is rate-limited or abandoned still consumed the quota. Sequential
#: calls keep the spend proportional to what actually got used, and make the
#: per-group progress visible while the report is being assembled.
MAX_CONCURRENT_GROUP_CALLS = 1


async def investigate_with_model(
    state: InvestigationState,
    model: BaseChatModel,
) -> dict[str, Any]:
    """Explain each error group with its own focused model call.

    One call per group rather than one call for the whole report. A reasoning
    model asked for N findings with ten fields each has a large task to think
    through and can exhaust its output budget before answering; one group
    against a handful of cited lines is a small task that finishes.

    Calls run concurrently and are independent: a group whose call fails or
    returns junk is dropped with a warning, and the remaining groups still
    produce findings.
    """
    started = _started(state)
    groups = state.get("groups") or []
    evidence = state.get("evidence")
    cfg = state.get("settings")
    _SETTINGS_CACHE["stagger"] = float(cfg.model_call_stagger_seconds)
    _SETTINGS_CACHE["is_test"] = bool(cfg.is_test)

    base: dict[str, Any] = {"validation_errors": [], "repair_attempted": False}

    if not groups:
        return {
            **base,
            "processing_steps": _record(
                state, "investigate_with_model", "skipped", started,
                "No error groups found; nothing to investigate",
            ),
            "findings": [],
            "raw_findings": [],
            "model_call_failures": [],
            "ai_skipped_reason": "No error groups were detected in the input.",
        }

    if evidence is None or not evidence.items:  # pragma: no cover - defensive
        return {
            **base,
            "processing_steps": _record(
                state, "investigate_with_model", "skipped", started,
                "No evidence available to send to the model",
            ),
            "findings": [],
            "raw_findings": [],
            "model_call_failures": [],
            "ai_skipped_reason": "No evidence could be selected for the model.",
        }

    # Bound how many groups are explained. Every group costs a model call, so
    # this setting is what keeps spend proportional to the setting the operator
    # chose rather than to the size of their log file.
    limit = int(state.get("settings").max_groups_for_model)
    targets = groups[:limit]
    skipped_for_budget = len(groups) - len(targets)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_GROUP_CALLS)

    async def explain(group: Any) -> tuple[Any, dict[str, Any] | None, str | None]:
        """Run one focused call for one group."""
        items = evidence_for_group(group, evidence)
        messages = [
            ChatMessage(role="system", content=GROUP_SYSTEM_PROMPT),
            ChatMessage(role="user", content=build_group_prompt(group=group, items=items)),
        ]
        async with semaphore:
            try:
                response = await model.complete(messages)
            except (ModelTimeoutError, ModelUnavailableError, ModelError) as exc:
                return group, None, f"{type(exc).__name__}: {exc}"

        parsed, decode_error = _decode(response.text)
        if parsed is None:
            # One constrained retry for malformed JSON, not a full repair cycle.
            try:
                retry = await model.complete([
                    ChatMessage(role="system", content=GROUP_SYSTEM_PROMPT),
                    ChatMessage(
                        role="user",
                        content=build_repair_prompt(
                            raw_output=response.text,
                            validation_errors=[decode_error or "unparseable response"],
                        ),
                    ),
                ])
            except (ModelTimeoutError, ModelUnavailableError, ModelError) as exc:
                return group, None, f"unparseable, retry failed ({type(exc).__name__})"
            parsed, decode_error = _decode(retry.text)

        if parsed is None:
            return group, None, decode_error or "unparseable response"

        # The caller knows exactly which group this call was for. Stamping the
        # fingerprint here removes any need to re-match on title, which is
        # ambiguous when two groups share a message on different call paths.
        parsed = {**parsed, "fingerprint": group.fingerprint}
        return group, parsed, None

    results: list[tuple[Any, dict[str, Any] | None, str | None]] = []
    for group in targets:
        # Pace every call, including the first: a burst of N identical requests
        # on open is exactly what gets an API key rate limited.
        await asyncio.sleep(_stagger_delay())
        results.append(await explain(group))

    raw_findings: list[dict[str, Any]] = []
    failures: list[str] = []
    for group, parsed, error in results:
        if parsed is not None:
            raw_findings.append(parsed)
        elif error is not None:
            failures.append(f"{group.title[:60]}: {error}")

    # The report summary is derived deterministically, not asked for, so it is
    # reproducible and costs no call.
    summary = _deterministic_summary(groups)

    if skipped_for_budget:
        failures.append(
            f"{skipped_for_budget} further group(s) were not explained because "
            f"MAX_GROUPS_FOR_MODEL is {limit}."
        )

    step_status = "completed" if raw_findings else "failed"
    note = (
        f"Explained {len(raw_findings)}/{len(targets)} group(s) via focused calls"
        + (f"; {len(failures)} issue(s)" if failures else "")
    )

    update: dict[str, Any] = {
        **base,
        "processing_steps": _record(state, "investigate_with_model", step_status, started, note),
        "raw_findings": raw_findings,
        "model_call_failures": failures,
        "model_summary": summary,
        "model_response": " ".join(json.dumps(f) for f in raw_findings[:3]) or None,
        "model_metadata": {"provider": model.provider_name},
    }

    if raw_findings:
        return update

    # Nothing survived: treat as an AI failure so the deterministic report stands.
    update["ai_skipped_reason"] = (
        failures[0] if failures else "The model returned nothing usable for any group."
    )
    return update


def _stagger_delay() -> float:
    """Pause between calls, with jitter so the pattern is not metronomic.

    Not cryptographic; jitter here only avoids a metronomic request pattern.
    Returns 0 under APP_ENV=test so the suite does not spend its time sleeping.
    """
    if _SETTINGS_CACHE.get("is_test"):
        return 0.0
    base = _SETTINGS_CACHE.get("stagger", 1.5)
    return base * random.uniform(0.7, 1.3)  # noqa: S311


#: Populated per run from settings by investigate_with_model.
_SETTINGS_CACHE: dict[str, Any] = {"stagger": 1.5, "is_test": False}


def _deterministic_summary(groups: list[Any]) -> str:
    """Build the report summary from deterministic facts only."""
    if not groups:
        return "No error groups were identified."
    top = groups[0]
    total = sum(g.count for g in groups)
    return (
        f"{len(groups)} recurring error group(s) covering {total} event(s). "
        f"The most frequent is '{top.title}' ({top.count} occurrences, "
        f"{top.severity.value} severity)."
    )


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
    """Validate each per-group finding independently (SPEC §8.3).

    A finding that cites nothing real, or cites another group's lines, is
    rejected on its own. The rest are kept, and the rejected ones are recorded
    by fingerprint so ``bounded_repair`` can retry just those.
    """
    started = _started(state)
    groups = state.get("groups") or []
    evidence = state.get("evidence")
    valid_ids = {item.id for item in (evidence.items if evidence else ())}
    raw_findings = state.get("raw_findings") or []

    if state.get("model_response") is None and not raw_findings:
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
            "rejected_fingerprints": [],
        }

    outcome = validate_findings_batch(
        raw_findings, groups=groups, valid_evidence_ids=valid_ids
    )

    # Which supplied groups ended up with no accepted finding.
    answered = {f.fingerprint for f in outcome.findings}
    rejected = [
        g.fingerprint
        for g in groups
        if g.fingerprint not in answered and _covers_group(g, raw_findings, valid_ids)
    ]

    summary_text = (
        f"Accepted {len(outcome.findings)} of {len(raw_findings)} finding(s)"
        + (f"; rejected {len(rejected)} for one retry" if rejected else "")
    )

    return {
        "processing_steps": _record(
            state, "validate_findings", "completed", started, summary_text
        ),
        "findings": [f.model_dump(mode="json") for f in outcome.findings],
        "validation_errors": outcome.errors,
        "rejected_fingerprints": rejected,
        "report": {
            "summary": state.get("model_summary"),
            "findings": raw_findings,
            "limitations": state.get("model_call_failures") or [],
        },
    }


def _covers_group(
    group: Any, raw_findings: list[dict[str, Any]], valid_ids: set[str]
) -> bool:
    """Whether the model attempted this group but the attempt was rejected."""
    from app.graph.validation import _match_group  # local: internal helper

    seen: dict[str, Any] = {}
    return any(_match_group(raw, [group], seen) is not None for raw in raw_findings)


# ---------------------------------------------------------------------------
# 8. bounded_repair
# ---------------------------------------------------------------------------


async def bounded_repair(state: InvestigationState, model: BaseChatModel) -> dict[str, Any]:
    """Retry once, and only for the groups that were rejected (SPEC §8.3).

    The per-group calls already retry malformed JSON inline. This node exists for
    findings that parsed but failed validation — a fabricated citation, a wrong
    confidence. It re-asks just those groups, with the specific complaint, and
    never retries more than once.
    """
    started = _started(state)
    rejected = state.get("rejected_fingerprints") or []
    groups = [g for g in (state.get("groups") or []) if g.fingerprint in set(rejected)]
    evidence = state.get("evidence")
    errors = state.get("validation_errors") or []
    raw_findings = list(state.get("raw_findings") or [])

    if not groups or evidence is None:
        return {"findings": [], "repair_attempted": True}

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_GROUP_CALLS)

    async def retry(group: Any) -> tuple[Any, dict[str, Any] | None]:
        items = evidence_for_group(group, evidence)
        async with semaphore:
            try:
                response = await model.complete([
                    ChatMessage(role="system", content=GROUP_SYSTEM_PROMPT),
                    ChatMessage(
                        role="user",
                        content=build_group_prompt(group=group, items=items)
                        + "\n\nYour previous answer was rejected:\n"
                        + "\n".join(f"- {e}" for e in errors[:5]),
                    ),
                ])
            except (ModelTimeoutError, ModelUnavailableError, ModelError):
                return group, None
        parsed, _ = _decode(response.text)
        if parsed is not None:
            parsed = {**parsed, "fingerprint": group.fingerprint}
        return group, parsed

    results = await asyncio.gather(*(retry(g) for g in groups))
    repaired = [parsed for _g, parsed in results if parsed is not None]

    note = (
        f"Retried {len(groups)} rejected group(s); recovered {len(repaired)}"
        if groups
        else "Nothing to retry"
    )

    return {
        "processing_steps": _record(
            state,
            "bounded_repair",
            "completed" if repaired else "failed",
            started,
            note,
        ),
        "raw_findings": [*raw_findings, *repaired],
        "repair_attempted": True,
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


def build_fallback_findings(
    groups: list[Any],
    *,
    valid_evidence_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Build deterministic findings when the model produced nothing usable.

    Used when inference failed or every finding was rejected (SPEC §8.5). These
    are explicitly derived from the deterministic analysis, not from the model,
    so the report stays honest about its origin.
    """
    findings: list[dict[str, Any]] = []
    for group in groups[:8]:
        # Only cite lines the report actually carries, so every citation in the
        # UI resolves to evidence the user can open.
        citations = [eid for eid in group.evidence_ids if valid_evidence_ids is None
                     or eid in valid_evidence_ids][:4]
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
                "evidence_ids": citations,
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
