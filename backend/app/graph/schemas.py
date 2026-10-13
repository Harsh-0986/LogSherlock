"""AI output contract (SPEC §9.1).

The model must produce JSON matching these schemas. Everything here is
untrusted until it has passed validation, and validation is *deterministic* —
the prompt asking for good output is not the enforcement mechanism
(SPEC §9.3).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Bound the model's output so a runaway generation cannot dominate the report.
MAX_TITLE = 200
MAX_TEXT = 2000
MAX_LIST_ITEMS = 8
MAX_STEPS = 6
MAX_ALTERNATIVES = 5
MAX_MISSING = 8


class Confidence(BaseModel):
    """Allowed confidence levels."""

    low: str = "low"
    medium: str = "medium"
    high: str = "high"


class RecommendedStep(BaseModel):
    """One concrete next debugging action."""

    action: str = Field(min_length=1, max_length=MAX_TITLE)
    reason: str = Field(default="", max_length=MAX_TEXT)
    verification: str = Field(default="", max_length=MAX_TEXT)


class Finding(BaseModel):
    """A single validated finding about one error group."""

    model_config = ConfigDict(extra="ignore")

    fingerprint: str = Field(
        min_length=1,
        max_length=64,
        description="Fingerprint of the error group this finding explains.",
    )
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    severity: str = Field(default="medium", max_length=16)
    likely_cause: str = Field(min_length=1, max_length=MAX_TEXT)
    confidence: str = Field(default="low", max_length=16)
    evidence_ids: list[str] = Field(
        default_factory=list,
        max_length=40,
        description="Citation ids supplied in the prompt. May not invent new ones.",
    )
    reasoning_summary: str = Field(default="", max_length=MAX_TEXT)
    alternative_hypotheses: list[str] = Field(default_factory=list, max_length=MAX_ALTERNATIVES)
    missing_information: list[str] = Field(default_factory=list, max_length=MAX_MISSING)
    recommended_steps: list[RecommendedStep] = Field(default_factory=list, max_length=MAX_STEPS)

    @field_validator("severity", "confidence")
    @classmethod
    def _lowercase(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("confidence")
    @classmethod
    def _known_confidence(cls, value: str) -> str:
        if value not in {"low", "medium", "high"}:
            raise ValueError(f"confidence must be low|medium|high, got {value!r}")
        return value


class ModelReport(BaseModel):
    """Top-level structured report returned by the model."""

    model_config = ConfigDict(extra="ignore")

    summary: str = Field(min_length=1, max_length=MAX_TEXT)
    findings: list[Finding] = Field(default_factory=list, max_length=MAX_LIST_ITEMS * 2)
    limitations: list[str] = Field(default_factory=list, max_length=MAX_MISSING)
