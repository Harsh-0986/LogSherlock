"""Deterministic analysis pipeline (SPEC §7).

parse -> redact -> normalize -> group -> rank -> select evidence.

None of this touches the model. Phase 2 acceptance is that a sample file yields
correct, repeatable error groups with no model connection at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import Settings
from app.logengine.detect import detect_runtime, get_parser
from app.logengine.evidence import select_evidence
from app.logengine.grouping import apply_normalized_messages, group_events
from app.logengine.models import (
    DeterministicSummary,
    ErrorGroup,
    EvidenceBundle,
    LogEvent,
    LogLevel,
    Runtime,
    Severity,
)
from app.logengine.redaction import Redactor
from app.logengine.severity import rank_groups


@dataclass(slots=True)
class DeterministicResult:
    """Everything the model-independent half of an investigation produces."""

    summary: DeterministicSummary
    events: list[LogEvent]
    groups: list[ErrorGroup]
    evidence: EvidenceBundle
    warnings: list[str] = field(default_factory=list)

    @property
    def error_groups(self) -> list[ErrorGroup]:
        """Groups at ERROR/CRITICAL severity."""
        return [
            g
            for g in self.groups
            if g.severity in {Severity.HIGH, Severity.CRITICAL, Severity.MEDIUM}
        ]

    @property
    def warning_groups(self) -> list[ErrorGroup]:
        """Groups at WARN severity."""
        return [g for g in self.groups if g.severity is Severity.LOW]


def analyze(
    *,
    text: str,
    settings: Settings,
    runtime: Runtime = Runtime.AUTO,
) -> DeterministicResult:
    """Run the full deterministic analysis over ``text``.

    Never raises for content problems: unparsable input degrades to the generic
    parser and the limitation is recorded in ``warnings`` (SPEC §8.5).
    """
    warnings: list[str] = []

    # --- 1. Detect / choose format -------------------------------------
    # Only AUTO triggers detection. An explicitly requested runtime — including
    # GENERIC — is honoured as-is, so a caller can force the generic parser.
    detected = detect_runtime(text) if runtime is Runtime.AUTO else runtime

    if runtime is Runtime.AUTO and detected is Runtime.GENERIC:
        warnings.append(
            "Log format was not recognised; fell back to generic parsing. "
            "Fields such as exception type and service may be missing."
        )

    parser = get_parser(detected)

    # --- 2. Parse -------------------------------------------------------
    try:
        events = parser.parse(text)
    except Exception as exc:
        warnings.append(
            f"Parser {detected.value} failed ({type(exc).__name__}); used generic parser."
        )
        events = get_parser(Runtime.GENERIC).parse(text)
        detected = Runtime.GENERIC

    for event in events:
        warnings.extend(f"Line {event.line_number}: {w}" for w in event.warnings)

    # --- 3. Redact in place on the event copy --------------------------
    redactor = Redactor(
        enabled=settings.redaction_enabled,
        redact_ip_addresses=settings.redact_ip_addresses,
    )
    redaction_count = 0
    redacted_events: list[LogEvent] = []
    for event in events:
        redacted_raw = redactor.redact(event.raw_text)
        if redacted_raw.changed:
            redaction_count += redacted_raw.total_redactions
        redacted_events.append(
            event.model_copy(
                update={
                    "redacted_text": redacted_raw.text,
                    "message": redactor.redact(event.message).text,
                }
            )
        )
    events = redacted_events

    if redaction_count:
        warnings.append(
            f"Redacted {redaction_count} potentially sensitive value(s) before analysis. "
            "Pattern-based redaction is best-effort and cannot detect every secret."
        )

    # --- 4. Normalize + group -----------------------------------------
    events = apply_normalized_messages(events)
    groups = rank_groups(group_events(events))

    # --- 5. Evidence ----------------------------------------------------
    evidence = select_evidence(
        events=events,
        groups=groups,
        redactor=redactor,
        max_chars=settings.max_evidence_chars,
        max_groups=settings.max_groups_for_model,
    )
    if evidence.truncated and evidence.note:
        warnings.append(evidence.note)

    # --- 6. Summary -----------------------------------------------------
    error_count = sum(
        1 for g in groups if g.severity in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}
    )
    warning_count = sum(1 for g in groups if g.severity is Severity.LOW)
    summary = DeterministicSummary(
        total_lines=len(text.splitlines()),
        total_events=len(events),
        error_group_count=error_count,
        warning_group_count=warning_count,
        runtime=runtime,
        detected_runtime=detected,
    )

    return DeterministicResult(
        summary=summary,
        events=events,
        groups=groups,
        evidence=evidence,
        warnings=warnings,
    )


def redact_event_text(event: LogEvent) -> str:
    """Return the text that is safe to display or send externally."""
    return event.redacted_text if event.redacted_text is not None else event.raw_text


__all__ = [
    "DeterministicResult",
    "LogLevel",
    "analyze",
    "redact_event_text",
]
