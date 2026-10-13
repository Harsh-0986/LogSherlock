"""Deterministic log engine: parsing, normalization, grouping, evidence.

This package never imports the model layer, so it is independently testable
(SPEC §5.2).
"""

from app.logengine.models import (
    DeterministicSummary,
    ErrorGroup,
    EvidenceBundle,
    EvidenceItem,
    LogEvent,
    LogLevel,
    Runtime,
    Severity,
)
from app.logengine.pipeline import DeterministicResult, analyze
from app.logengine.redaction import RedactionResult, Redactor

__all__ = [
    "DeterministicResult",
    "DeterministicSummary",
    "ErrorGroup",
    "EvidenceBundle",
    "EvidenceItem",
    "LogEvent",
    "LogLevel",
    "RedactionResult",
    "Redactor",
    "Runtime",
    "Severity",
    "analyze",
]
