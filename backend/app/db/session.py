"""Database engine and session management (SPEC §11).

SQLite for the MVP. The engine is created lazily so importing the app does not
touch the filesystem.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

_engines: dict[str, Engine] = {}
_session_factories: dict[str, sessionmaker[Session]] = {}


def build_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create (and cache) an engine for ``database_url``."""
    if database_url in _engines:
        return _engines[database_url]

    connect_args: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        # FastAPI serves requests from a thread pool; SQLite needs this relaxed.
        connect_args["check_same_thread"] = False

    engine = create_engine(database_url, echo=echo, future=True, connect_args=connect_args)

    if database_url.startswith("sqlite"):
        _enable_sqlite_pragmas(engine)

    _engines[database_url] = engine
    return engine


def _enable_sqlite_pragmas(engine: Engine) -> None:
    """Enable foreign keys (off by default in SQLite) and WAL."""

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            # Cascading deletes in SPEC 11.5 depend on this being ON.
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()


def get_session_factory(database_url: str) -> sessionmaker[Session]:
    """Return (and cache) a session factory for ``database_url``."""
    if database_url not in _session_factories:
        engine = build_engine(database_url)
        _session_factories[database_url] = sessionmaker(
            bind=engine, autoflush=False, expire_on_commit=False, future=True
        )
    return _session_factories[database_url]


def create_all(database_url: str) -> None:
    """Create every table. Alembic owns migrations; this is a safety net."""
    Base.metadata.create_all(build_engine(database_url))


@contextmanager
def session_scope(database_url: str) -> Iterator[Session]:
    """Yield a session inside a transaction, committing on success."""
    factory = get_session_factory(database_url)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_all() -> None:
    """Close every cached engine. Used on shutdown and in tests."""
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()
    _session_factories.clear()
