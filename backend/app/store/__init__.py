"""Persistence seam and implementations (SPEC §11)."""

from app.store.base import InMemoryStore, InvestigationRecord, InvestigationStore
from app.store.factory import build_store

__all__ = [
    "InMemoryStore",
    "InvestigationRecord",
    "InvestigationStore",
    "build_store",
]
