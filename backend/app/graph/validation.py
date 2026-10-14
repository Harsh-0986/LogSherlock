"""Deterministic validation of model output (SPEC §9.2).

This module is the real enforcement boundary. The prompt asks for good output;
this code decides what is actually allowed through.

Four classes of fabrication are rejected:
1. **Unknown evidence ids** — a citation must resolve to a line we supplied.
2. **Unknown fingerprints** — a finding must refer to a group we computed.
3. **No citations at all** — a log-derived claim with no evidence is discarded.
4. **Citations from another group** — a finding cannot lean on a neighbour's
   lines as if they were its own.

Each finding is validated independently, so one bad finding never discards the
others.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.graph.schemas import Finding, ModelReport
from app.logengine.models import ErrorGroup

VALID_CONFIDENCE = frozenset({"low", "medium", "high"})
VALID_SEVERITY = frozenset({"critical", "high", "medium", "low", "info"})


@dataclass(slots=True)
class ValidationOutcome:
    """Result of validating model output against deterministic facts."""

    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    discarded: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing at all had to be rejected."""
        return not self.errors

    @property
    def usable(self) -> bool:
        """True when there is at least one finding worth showing."""
        return bool(self.findings)


def validate_finding(
    raw: dict[str, object],
    *,
    group: ErrorGroup,
    valid_evidence_ids: set[str],
) -> tuple[Finding | None, list[str]]:
    """Validate one finding against one group.

    Returns:
        The accepted finding (with severity forced to the deterministic value)
        and the list of reasons it was rejected. The finding is ``None`` when
        any reason is present.
    """
    errors: list[str] = []

    # The model does not get to choose which group it explains; we know.
    raw = {**raw, "fingerprint": group.fingerprint}

    try:
        finding = Finding.model_validate(raw)
    except Exception as exc:  # pydantic raises ValidationError
        errors.append(f"{group.title[:40]!r}: schema invalid ({_short(exc)})")
        return None, errors

    errors.extend(_problems(finding, group=group, valid_evidence_ids=valid_evidence_ids))
    if errors:
        return None, errors

    # Severity is deterministic; the model may comment but not overwrite it.
    return (
        finding.model_copy(
            update={
                "severity": group.severity.value,
                "evidence_ids": [
                    e for e in finding.evidence_ids if e in valid_evidence_ids
                ],
            }
        ),
        errors,
    )


def validate_findings(
    raw_findings: list[dict[str, object]],
    *,
    groups: list[ErrorGroup],
    valid_evidence_ids: set[str],
) -> ValidationOutcome:
    """Validate a batch of per-group findings independently.

    A group the model failed to address simply produces no finding; it is not an
    error, because partial coverage is expected and useful.
    """
    outcome = ValidationOutcome()
    by_title: dict[str, ErrorGroup] = {}

    for raw in raw_findings:
        if not isinstance(raw, dict):
            outcome.errors.append("Model returned a non-object finding.")
            continue

        group = _match_group(raw, groups, by_title)
        if group is None:
            outcome.errors.append(
                f"Finding {str(raw.get('title', ''))[:40]!r} does not correspond to any "
                "supplied error group."
            )
            continue

        finding, problems = validate_finding(
            raw, group=group, valid_evidence_ids=valid_evidence_ids
        )
        if finding is not None:
            outcome.findings.append(finding)
        else:
            outcome.errors.extend(problems)

    return outcome


def _match_group(
    raw: dict[str, object],
    groups: list[ErrorGroup],
    seen: dict[str, ErrorGroup],
) -> ErrorGroup | None:
    """Resolve which supplied group a raw finding is about.

    Per-group calls stamp the fingerprint themselves, so that is the normal
    path. Title matching is only a fallback for a response that arrived without
    one, and it never guesses when nothing lines up.
    """
    fingerprint = raw.get("fingerprint")
    if isinstance(fingerprint, str):
        for group in groups:
            if group.fingerprint == fingerprint:
                return group

    title = str(raw.get("title", "")).strip().lower()
    if not title:
        return None

    if title in seen:
        return seen[title]

    for group in groups:
        if group.title.strip().lower() == title:
            seen[title] = group
            return group

    # Models often reword the title. Fall back to a distinctive prefix.
    prefix = " ".join(title.split()[:5])
    for group in groups:
        if prefix and group.title.strip().lower().startswith(prefix):
            seen[title] = group
            return group

    return None


def _problems(
    finding: Finding,
    *,
    group: ErrorGroup,
    valid_evidence_ids: set[str],
) -> list[str]:
    """Return the reasons a finding is unusable. Empty means valid."""
    problems: list[str] = []

    fabricated = [e for e in finding.evidence_ids if e not in valid_evidence_ids]
    if fabricated:
        problems.append(
            f"{group.title[:40]!r} cites evidence ids that were never supplied: "
            f"{fabricated[:5]}"
        )

    if not finding.evidence_ids:
        problems.append(f"{group.title[:40]!r} cites no evidence.")

    if finding.confidence not in VALID_CONFIDENCE:
        problems.append(
            f"{group.title[:40]!r} has invalid confidence {finding.confidence!r}."
        )

    # A finding may cite context lines attached to its own group, but it must
    # cite at least one of the group's own event lines.
    group_lines = set(group.evidence_ids)
    if finding.evidence_ids and not any(e in group_lines for e in finding.evidence_ids):
        problems.append(
            f"{group.title[:40]!r} cites only evidence outside its own group."
        )

    return problems


def validate_report(
    raw: dict[str, object],
    *,
    groups: list[ErrorGroup],
    valid_evidence_ids: set[str],
) -> ValidationOutcome:
    """Validate a single-report-shaped response (retained for compatibility)."""
    outcome = ValidationOutcome()
    try:
        report = ModelReport.model_validate(raw)
    except Exception as exc:  # pydantic raises ValidationError
        outcome.errors.append(f"Schema validation failed: {_short(exc)}")
        return outcome

    by_fp = {g.fingerprint: g for g in groups}
    for finding in report.findings:
        group = by_fp.get(finding.fingerprint)
        if group is None:
            outcome.errors.append(
                f"Finding {finding.title[:40]!r} cites unknown fingerprint "
                f"{finding.fingerprint!r}."
            )
            outcome.discarded.append(finding)
            continue
        accepted, problems = validate_finding(
            finding.model_dump(mode="json"),
            group=group,
            valid_evidence_ids=valid_evidence_ids,
        )
        if accepted is not None:
            outcome.findings.append(accepted)
        else:
            outcome.errors.extend(problems)
            outcome.discarded.append(finding)
    return outcome


def _short(exc: Exception, limit: int = 300) -> str:
    """Trim a pydantic error message so it can go in the repair prompt."""
    return str(exc).replace("\n", " ")[:limit]
