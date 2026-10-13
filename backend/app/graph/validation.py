"""Deterministic validation of model output (SPEC §9.2).

This module is the real enforcement boundary. The prompt asks for valid output;
this code decides what is actually allowed through.

Three classes of fabrication are rejected:
1. **Unknown evidence ids** — a citation must resolve to a line we supplied.
2. **Unknown fingerprints** — a finding must refer to a group we computed.
3. **Count/severity contradictions** — the model must not overwrite the
   deterministic observation (SPEC §7.7).
"""

from __future__ import annotations

from app.graph.schemas import Finding, ModelReport
from app.logengine.models import ErrorGroup

VALID_CONFIDENCE = frozenset({"low", "medium", "high"})
VALID_SEVERITY = frozenset({"critical", "high", "medium", "low", "info"})


class ValidationOutcome:
    """Result of validating model output against deterministic facts."""

    def __init__(self) -> None:
        """Initialize an empty outcome."""
        self.errors: list[str] = []
        self.findings: list[Finding] = []
        self.discarded: list[Finding] = []

    @property
    def ok(self) -> bool:
        """True when the output can be used as-is."""
        return not self.errors

    @property
    def usable(self) -> bool:
        """True when there is at least one finding worth showing."""
        return bool(self.findings)


def validate_report(
    raw: dict[str, object],
    *,
    groups: list[ErrorGroup],
    valid_evidence_ids: set[str],
) -> ValidationOutcome:
    """Validate a decoded model report against the deterministic analysis.

    Structural problems (bad JSON shape) land in ``errors``. Individual findings
    that merely misbehave are dropped into ``discarded`` so one bad finding does
    not throw away the whole report.
    """
    outcome = ValidationOutcome()

    # --- Structural validation -----------------------------------------
    try:
        report = ModelReport.model_validate(raw)
    except Exception as exc:
        outcome.errors.append(f"Schema validation failed: {_short(exc)}")
        return outcome

    group_by_fp = {g.fingerprint: g for g in groups}

    for finding in report.findings:
        problems = _validate_finding(
            finding,
            group_by_fp=group_by_fp,
            valid_evidence_ids=valid_evidence_ids,
        )
        if problems:
            outcome.discarded.append(finding)
            outcome.errors.extend(problems)
            continue

        group = group_by_fp[finding.fingerprint]
        # Force the deterministic values. The model may comment on severity,
        # but the reported severity must be the one we computed.
        outcome.findings.append(
            finding.model_copy(
                update={
                    "severity": group.severity.value,
                    "evidence_ids": [
                        eid for eid in finding.evidence_ids if eid in valid_evidence_ids
                    ],
                }
            )
        )

    return outcome


def _validate_finding(
    finding: Finding,
    *,
    group_by_fp: dict[str, ErrorGroup],
    valid_evidence_ids: set[str],
) -> list[str]:
    """Return the reasons a single finding is unusable. Empty means valid."""
    problems: list[str] = []

    # 1. Fabricated group reference.
    if finding.fingerprint not in group_by_fp:
        problems.append(
            f"Finding {finding.title!r} cites unknown fingerprint {finding.fingerprint!r}; "
            f"valid fingerprints: {sorted(group_by_fp)[:8]}"
        )
        return problems  # nothing else can be checked without a group

    group = group_by_fp[finding.fingerprint]

    # 2. Fabricated evidence references.
    fabricated = [eid for eid in finding.evidence_ids if eid not in valid_evidence_ids]
    if fabricated:
        problems.append(
            f"Finding {finding.title!r} cites evidence ids that were never supplied: "
            f"{fabricated[:5]}"
        )

    # 3. No evidence at all for a log-derived claim.
    if not finding.evidence_ids:
        problems.append(f"Finding {finding.title!r} cites no evidence.")

    # 4. Confidence outside the allowed set (also enforced by pydantic, but the
    #    repaired/partial path needs it too).
    if finding.confidence not in VALID_CONFIDENCE:
        problems.append(f"Finding {finding.title!r} has invalid confidence {finding.confidence!r}.")

    # 5. The cited evidence must belong to this group, or be a context line the
    #    backend attached to it. A finding cannot lean on a *different* group's
    #    lines as if they were its own.
    group_ids = set(group.evidence_ids)
    stray = [
        eid for eid in finding.evidence_ids if eid in valid_evidence_ids and eid not in group_ids
    ]
    if stray and not _has_any_group_line(finding, group_ids):
        problems.append(f"Finding {finding.title!r} cites only evidence outside its own group.")

    return problems


def _has_any_group_line(finding: Finding, group_ids: set[str]) -> bool:
    """Whether the finding cites at least one line from its own group."""
    return any(eid in group_ids for eid in finding.evidence_ids)


def _short(exc: Exception, limit: int = 400) -> str:
    """Trim a pydantic error message so it can go in the repair prompt."""
    text = str(exc).replace("\n", " ")
    return text[:limit]
