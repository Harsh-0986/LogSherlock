"""Store factory (SPEC §11).

Selects the persistence implementation. Phase 5 swaps the default to
SQLAlchemy; the in-process store remains available for tests and for running
without a database file.
"""

from __future__ import annotations

from app.config import Settings
from app.store.base import InMemoryStore, InvestigationStore


def build_store(settings: Settings) -> InvestigationStore:
    """Return the configured store."""
    if settings.app_env == "test" or settings.database_url.startswith("memory"):
        return InMemoryStore()
    return build_sqlalchemy_store(settings)


def build_sqlalchemy_store(settings: Settings) -> InvestigationStore:
    """Return the SQLAlchemy-backed store.

    Implemented in Phase 5. Falls back to the in-process store until then so the
    API stays usable in the meantime.
    """
    try:
        from app.store.sqlalchemy_store import SqlAlchemyStore
    except ImportError:  # pragma: no cover - before Phase 5 lands
        return InMemoryStore()
    return SqlAlchemyStore(settings.database_url)
