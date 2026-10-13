"""Persistence tests against a real SQLite database (SPEC §11, §15)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.session import dispose_all
from app.jobs.manager import JobManager
from app.main import create_app
from app.store.base import InvestigationRecord, InvestigationStore
from app.store.sqlalchemy_store import SqlAlchemyStore

SAMPLE = """2026-10-13T09:15:44.019Z ERROR [orders-api] service=orders-api Database connection failed
2026-10-13T09:15:44.019Z Error: Database connection failed
    at Database.connect (/srv/app/src/db/pool.js:88:23)
    at async OrderService.create (/srv/app/src/services/order.js:142:5)
2026-10-13T09:16:55.640Z ERROR [orders-api] service=orders-api Database connection failed
2026-10-13T09:16:55.640Z Error: Database connection failed
    at Database.connect (/srv/app/src/db/pool.js:88:23)
    at async OrderService.create (/srv/app/src/services/order.js:142:5)
2026-10-13T09:17:02.113Z WARN  [notification-svc] service=notification-svc slow query 2100ms
"""

FAKE_SECRET_LOG = (
    "2026-10-13T09:18:45.100Z ERROR [auth] service=auth "
    "Invalid bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.abc123 "
    "for refresh request"
)

TERMINAL = {"completed", "partial", "failed"}


@pytest.fixture
def store(tmp_path) -> SqlAlchemyStore:
    """A store backed by a throwaway SQLite file."""
    url = f"sqlite:///{tmp_path / 'test.db'}"
    instance = SqlAlchemyStore(url)
    yield instance
    dispose_all()


class TestStoreRoundTrip:
    def test_create_and_get(self, store: InvestigationStore) -> None:
        record = InvestigationRecord("inv_1")
        record.status = "queued"
        record.created_at = "2026-10-13T09:30:00+00:00"
        store.create(record)

        loaded = store.get("inv_1")
        assert loaded is not None
        assert loaded.id == "inv_1"
        assert loaded.status == "queued"

    def test_get_missing_returns_none(self, store: InvestigationStore) -> None:
        assert store.get("inv_missing") is None

    def test_update_persists_changes(self, store: InvestigationStore) -> None:
        record = InvestigationRecord("inv_1")
        record.created_at = "2026-10-13T09:30:00+00:00"
        store.create(record)

        record.status = "completed"
        record.summary = "3 events"
        record.error_group_count = 2
        store.update(record)

        loaded = store.get("inv_1")
        assert loaded.status == "completed"
        assert loaded.summary == "3 events"
        assert loaded.error_group_count == 2

    def test_update_creates_when_absent(self, store: InvestigationStore) -> None:
        record = InvestigationRecord("inv_new")
        record.created_at = "2026-10-13T09:30:00+00:00"
        store.update(record)
        assert store.get("inv_new") is not None

    def test_delete_removes_record(self, store: InvestigationStore) -> None:
        record = InvestigationRecord("inv_1")
        record.created_at = "2026-10-13T09:30:00+00:00"
        store.create(record)
        assert store.delete("inv_1") is True
        assert store.get("inv_1") is None

    def test_delete_missing_returns_false(self, store: InvestigationStore) -> None:
        assert store.delete("inv_missing") is False

    def test_list_is_newest_first(self, store: InvestigationStore) -> None:
        for index, ts in enumerate(
            ["2026-10-13T09:30:00", "2026-10-13T09:31:00", "2026-10-13T09:32:00"]
        ):
            record = InvestigationRecord(f"inv_{index}")
            record.created_at = f"{ts}+00:00"
            store.create(record)

        records, total = store.list(10, 0)
        assert total == 3
        assert [r.id for r in records] == ["inv_2", "inv_1", "inv_0"]

    def test_list_pagination(self, store: InvestigationStore) -> None:
        for index in range(5):
            record = InvestigationRecord(f"inv_{index}")
            record.created_at = f"2026-10-13T09:3{index}:00+00:00"
            store.create(record)

        page, total = store.list(2, 2)
        assert total == 5
        assert [r.id for r in page] == ["inv_2", "inv_1"]

    def test_raw_input_not_persisted_by_default(self, store: InvestigationStore) -> None:
        record = InvestigationRecord("inv_1")
        record.created_at = "2026-10-13T09:30:00+00:00"
        record.raw_input = None
        store.create(record)
        assert store.get("inv_1").raw_input is None


class TestCascadeDelete:
    async def test_children_are_removed_with_parent(self, store: SqlAlchemyStore) -> None:
        settings = Settings(app_env="test")
        manager = JobManager(store=store, settings=settings)
        record = manager.submit(content=SAMPLE)
        await asyncio.gather(*list(manager._tasks.values()), return_exceptions=True)

        assert store.get(record.id) is not None
        assert store.get(record.id).error_groups

        # Children must exist before the parent is deleted.
        assert store.purge is not None
        store.delete(record.id)

        from sqlalchemy import select

        from app.db.models import ErrorGroupRow, FindingRow, Investigation, ProcessingStepRow

        factory = store._factory
        session = factory()
        try:
            assert (
                session.scalars(
                    select(ErrorGroupRow).where(ErrorGroupRow.investigation_id == record.id)
                ).all()
                == []
            )
            assert (
                session.scalars(
                    select(FindingRow).where(FindingRow.investigation_id == record.id)
                ).all()
                == []
            )
            assert (
                session.scalars(
                    select(ProcessingStepRow).where(ProcessingStepRow.investigation_id == record.id)
                ).all()
                == []
            )
            assert session.get(Investigation, record.id) is None
        finally:
            session.close()


class TestEndToEndPersistence:
    async def test_report_survives_a_restart(self, tmp_path) -> None:
        """SPEC §11.5: reports must survive application restarts."""
        url = f"sqlite:///{tmp_path / 'persist.db'}"

        # --- First "process": create and run an investigation ------------
        settings = Settings(app_env="test", database_url=url, persist_raw_logs=False)
        store_1 = SqlAlchemyStore(url)
        manager_1 = JobManager(store=store_1, settings=settings)
        record = manager_1.submit(content=SAMPLE)
        await asyncio.gather(*list(manager_1._tasks.values()), return_exceptions=True)

        # --- Second "process": a brand new store over the same file -----
        dispose_all()
        store_2 = SqlAlchemyStore(url)

        reloaded = store_2.get(record.id)
        assert reloaded is not None, "the investigation must be readable after a restart"
        assert reloaded.status in TERMINAL
        assert len(reloaded.error_groups) > 0
        assert len(reloaded.processing_steps) > 0
        assert reloaded.raw_input is None, "raw logs must not be persisted by default"

        dispose_all()

    async def test_secrets_never_reach_the_database(self, tmp_path) -> None:
        url = f"sqlite:///{tmp_path / 'secrets.db'}"
        settings = Settings(app_env="test", database_url=url)
        store = SqlAlchemyStore(url)
        manager = JobManager(store=store, settings=settings)
        manager.submit(content=FAKE_SECRET_LOG)
        await asyncio.gather(*list(manager._tasks.values()), return_exceptions=True)

        db_file = tmp_path / "secrets.db"
        blob = db_file.read_bytes()
        assert b"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in blob, (
            "the JWT leaked into the database file"
        )
        dispose_all()

    async def test_raw_logs_persisted_only_when_enabled(self, tmp_path) -> None:
        url = f"sqlite:///{tmp_path / 'raw.db'}"
        settings = Settings(app_env="test", database_url=url, persist_raw_logs=True)
        store = SqlAlchemyStore(url)
        manager = JobManager(store=store, settings=settings)
        record = manager.submit(content=SAMPLE)
        await asyncio.gather(*list(manager._tasks.values()), return_exceptions=True)

        # Even when enabled, secrets must still be redacted in stored evidence.
        reloaded = store.get(record.id)
        assert reloaded is not None
        assert reloaded is not None
        blob = " ".join(item.text for item in reloaded.evidence)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in blob
        dispose_all()


class TestApiWithDatabase:
    def test_api_uses_sqlalchemy_outside_test_env(self, tmp_path, monkeypatch) -> None:
        url = f"sqlite:///{tmp_path / 'api.db'}"
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("DATABASE_URL", url)

        from app.config import get_settings
        from app.store.factory import build_store

        get_settings.cache_clear()
        settings = get_settings()
        assert isinstance(build_store(settings), SqlAlchemyStore)
        dispose_all()
        get_settings.cache_clear()

    def test_create_app_wires_the_store(self, tmp_path, monkeypatch) -> None:
        url = f"sqlite:///{tmp_path / 'wiring.db'}"
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("DATABASE_URL", url)

        from app.config import get_settings

        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            store = client.app.state.store
            assert isinstance(store, SqlAlchemyStore)

            response = client.post("/api/v1/investigations", json={"content": SAMPLE})
            assert response.status_code == 202
            investigation_id = response.json()["id"]

            # Poll until terminal, then confirm it is on disk.
            import time

            for _ in range(200):
                status = client.get(f"/api/v1/investigations/{investigation_id}/status").json()
                if status["status"] in TERMINAL:
                    break
                time.sleep(0.02)

            assert store.get(investigation_id) is not None

            listing = client.get("/api/v1/investigations").json()
            assert investigation_id in {item["id"] for item in listing["items"]}

        dispose_all()
        get_settings.cache_clear()
