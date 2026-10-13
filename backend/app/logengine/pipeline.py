"""Deterministic analysis pipeline (SPEC §7).

parse -> redact -> normalize -> group -> rank -> select evidence.

None of this touches the model. Phase 2 acceptance is that a sample file yields
correct, repeatable error groups with no model connection at all.

The work is exposed both as a single ``analyze()`` convenience call and as
individual stages, so the LangGraph nodes can each perform only their own step
rather than re-running the whole pipeline three times.
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
            if g.severity in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}
        ]

    @property
    def warning_groups(self) -> list[ErrorGroup]:
        """Groups at WARN severity."""
        return [g for g in self.groups if g.severity is Severity.LOW]


@dataclass(slots=True)
class ParsedStage:
    """Output of the parse stage."""

    events: list[LogEvent]
    detected_runtime: Runtime
    requested_runtime: Runtime
    total_lines: int
    warnings: list[str] = field(default_factory=list)


def build_redactor(settings: Settings) -> Redactor:
    """Return the configured redactor."""
    return Redactor(
        enabled=settings.redaction_enabled,
        redact_ip_addresses=settings.redact_ip_addresses,
    )


# ---------------------------------------------------------------------------
# Stage 1: parse (+ redact)
# ---------------------------------------------------------------------------


def stage_parse(*, text: str, runtime: Runtime = Runtime.AUTO) -> ParsedStage:
    """Detect the format, parse, and redact sensitive values.

    Never raises for content problems: unparsable input degrades to the generic
    parser and the limitation is recorded in ``warnings`` (SPEC §7.3, §8.5).
    """
    warnings: list[str] = []

    # Only AUTO triggers detection. An explicitly requested runtime — including
    # GENERIC — is honoured as-is, so a caller can force the generic parser.
    detected = detect_runtime(text) if runtime is Runtime.AUTO else runtime

    if runtime is Runtime.AUTO and detected is Runtime.GENERIC:
        warnings.append(
            "Log format was not recognised; fell back to generic parsing. "
            "Fields such as exception type and service may be missing."
        )

    try:
        events = get_parser(detected).parse(text)
    except Exception as exc:
        warnings.append(
            f"Parser {detected.value} failed ({type(exc).__name__}); used generic parser."
        )
        events = get_parser(Runtime.GENERIC).parse(text)
        detected = Runtime.GENERIC

    for event in events:
        warnings.extend(f"Line {event.line_number}: {w}" for w in event.warnings)

    return ParsedStage(
        events=events,
        detected_runtime=detected,
        requested_runtime=runtime,
        total_lines=len(text.splitlines()),
        warnings=warnings,
    )


def redact_events(
    events: list[LogEvent],
    redactor: Redactor,
) -> tuple[list[LogEvent], int]:
    """Redact every event, returning new events and the replacement count."""
    total = 0
    out: list[LogEvent] = []

    for event in events:
        redacted_raw = redactor.redact(event.raw_text)
        if redacted_raw.changed:
            total += redacted_raw.total_redactions
        out.append(
            event.model_copy(
                update={
                    "redacted_text": redacted_raw.text,
                    "message": redactor.redact(event.message).text,
                }
            )
        )

    return out, total


# ---------------------------------------------------------------------------
# Stage 2+3: normalize, group, rank
# ---------------------------------------------------------------------------


def stage_group(events: list[LogEvent]) -> list[LogEvent]:
    """Attach normalized messages to events (SPEC §7.5)."""
    return apply_normalized_messages(events)


def stage_rank(events: list[LogEvent]) -> list[ErrorGroup]:
    """Group events and rank the resulting groups by severity (SPEC §7.6, §7.7)."""
    return rank_groups(group_events(events))


# ---------------------------------------------------------------------------
# Convenience: the whole deterministic pipeline
# ---------------------------------------------------------------------------


def analyze(
    *,
    text: str,
    settings: Settings,
    runtime: Runtime = Runtime.AUTO,
) -> DeterministicResult:
    """Run every deterministic stage over ``text`` in order."""
    warnings: list[str] = []
    redactor = build_redactor(settings)

    parsed = stage_parse(text=text, runtime=runtime)
    warnings.extend(parsed.warnings)

    events, redaction_count = redact_events(parsed.events, redactor)
    if redaction_count:
        warnings.append(
            f"Redacted {redaction_count} potentially sensitive value(s) before analysis. "
            "Pattern-based redaction is best-effort and cannot detect every secret."
        )

    events = stage_group(events)
    groups = stage_rank(events)

    evidence = stage_evidence(events=events, groups=groups, redactor=redactor, settings=settings)
    if evidence.truncated and evidence.note:
        warnings.append(evidence.note)

    return DeterministicResult(
        summary=build_summary(parsed, groups, runtime),
        events=events,
        groups=groups,
        evidence=evidence,
        warnings=warnings,
    )


def stage_evidence(
    *,
    events: list[LogEvent],
    groups: list[ErrorGroup],
    redactor: Redactor,
    settings: Settings,
) -> EvidenceBundle:
    """Select the bounded evidence set for the model (SPEC §7.8)."""
    return select_evidence(
        events=events,
        groups=groups,
        redactor=redactor,
        max_chars=settings.max_evidence_chars,
        max_groups=settings.max_groups_for_model,
    )


def build_summary(
    parsed: ParsedStage,
    groups: list[ErrorGroup],
    runtime: Runtime,
) -> DeterministicSummary:
    """Build the deterministic summary from the parsed stage and groups."""
    error_count = sum(
        1 for g in groups if g.severity in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}
    )
    warning_count = sum(1 for g in groups if g.severity is Severity.LOW)

    return DeterministicSummary(
        total_lines=parsed.total_lines,
        total_events=len(parsed.events),
        error_group_count=error_count,
        warning_group_count=warning_count,
        runtime=runtime,
        detected_runtime=parsed.detected_runtime,
    )


def redact_event_text(event: LogEvent) -> str:
    """Return the text that is safe to display or send externally."""
    return event.redacted_text if event.redacted_text is not None else event.raw_text


__all__ = [
    "DeterministicResult",
    "LogLevel",
    "ParsedStage",
    "analyze",
    "build_redactor",
    "build_summary",
    "redact_event_text",
    "redact_events",
    "stage_evidence",
    "stage_group",
    "stage_parse",
    "stage_rank",
]
