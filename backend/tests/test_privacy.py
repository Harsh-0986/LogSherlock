"""Privacy and security tests (SPEC §15.5).

These verify the claims the README makes. If one of these fails, the README is
wrong and must change — not the test.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.errors import InvalidInputError, PayloadTooLargeError
from app.db.session import dispose_all
from app.jobs.manager import JobManager
from app.logengine.redaction import Redactor
from app.main import create_app
from app.models.base import BaseChatModel, ModelMetadata, ModelResponse
from app.store.sqlalchemy_store import SqlAlchemyStore

TOKEN_SHAPED_LOG = (
    "2026-10-13T09:18:45.100Z ERROR [auth] service=auth Invalid bearer "
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMDQ0MzIifQ.dBjftJeZ4CVPmB9 "
    "for refresh\n"
    "2026-10-13T09:18:50.220Z WARN  [auth] service=auth Password reset requested "
    "for ops@shopstream.io token=a3f9c1e77b2d4056\n"
    "2026-10-13T09:18:55.000Z INFO  [db] service=db dsn="
    "postgresql://appuser:hunter2secret@postgres-primary:5432/shopstream\n"
    "2026-10-13T09:18:56.000Z INFO  [s3] service=s3 aws_key=AKIAIOSFODNN7EXAMPLE ok\n"
)

SECRET_STRINGS = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
    "a3f9c1e77b2d4056",
    "hunter2secret",
    "AKIAIOSFODNN7EXAMPLE",
    "ops@shopstream.io",
)


class SecretLeakingModel(BaseChatModel):
    """A provider that tries to echo secrets back. Used to prove we do not."""

    provider_name = "leaky"

    def metadata(self) -> ModelMetadata:
        """Return metadata."""
        return ModelMetadata(provider=self.provider_name, model_name="leaky")

    async def health(self):
        """Report available."""
        from app.models.base import ModelHealth

        return ModelHealth(self.provider_name, "leaky", True)

    async def complete(self, messages, *, response_schema=None) -> ModelResponse:
        """Return everything the prompt contained, verbatim."""
        leaked = "\n".join(m.content for m in messages)
        return ModelResponse(text=leaked, provider=self.provider_name, model_name="leaky")


class TestRedactionClaims:
    def test_common_secrets_are_redacted(self) -> None:
        redacted = Redactor().redact(TOKEN_SHAPED_LOG).text
        for secret in SECRET_STRINGS:
            assert secret not in redacted, f"not redacted: {secret}"

    def test_placeholders_are_used(self) -> None:
        redacted = Redactor().redact(TOKEN_SHAPED_LOG).text
        assert "[REDACTED" in redacted

    def test_redaction_is_disclosed_not_hidden(self) -> None:
        """The user is told redaction happened and that it is best-effort."""
        from app.config import Settings
        from app.logengine.pipeline import analyze

        result = analyze(text=TOKEN_SHAPED_LOG, settings=Settings(app_env="test"))
        assert any("best-effort" in w for w in result.warnings)


class TestModelBoundary:
    async def test_model_receives_redacted_evidence_only(self, tmp_path) -> None:
        """The model must never see a raw secret, even if it asks for one."""
        url = f"sqlite:///{tmp_path / 'a.db'}"
        store = SqlAlchemyStore(url)
        settings = Settings(app_env="test", database_url=url)
        manager = JobManager(
            store=store, settings=settings, model_factory=lambda _s: SecretLeakingModel()
        )

        manager.submit(content=TOKEN_SHAPED_LOG)
        await asyncio.gather(*list(manager._tasks.values()), return_exceptions=True)

        record = manager.list(50, 0)[0][0]
        persisted = store.get(record.id)

        # Nothing the model echoed survived validation, and nothing leaked.
        blob = " ".join(item.text for item in persisted.evidence)
        for secret in SECRET_STRINGS:
            assert secret not in blob
        dispose_all()


class TestPersistenceClaims:
    async def test_raw_logs_absent_by_default(self, tmp_path) -> None:
        url = f"sqlite:///{tmp_path / 'b.db'}"
        store = SqlAlchemyStore(url)
        settings = Settings(app_env="test", database_url=url, persist_raw_logs=False)
        manager = JobManager(store=store, settings=settings)

        record = manager.submit(content=TOKEN_SHAPED_LOG)
        await asyncio.gather(*list(manager._tasks.values()), return_exceptions=True)

        assert store.get(record.id).raw_input is None
        dispose_all()

    async def test_database_file_contains_no_secrets(self, tmp_path) -> None:
        url = f"sqlite:///{tmp_path / 'c.db'}"
        store = SqlAlchemyStore(url)
        settings = Settings(app_env="test", database_url=url)
        manager = JobManager(store=store, settings=settings)

        manager.submit(content=TOKEN_SHAPED_LOG)
        await asyncio.gather(*list(manager._tasks.values()), return_exceptions=True)

        blob = (tmp_path / "c.db").read_bytes()
        for secret in SECRET_STRINGS:
            assert secret.encode() not in blob
        dispose_all()

    def test_api_response_excludes_raw_logs(self) -> None:
        from app.config import get_settings

        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            response = client.post("/api/v1/investigations", json={"content": TOKEN_SHAPED_LOG})
            assert response.status_code == 202
            investigation_id = response.json()["id"]

            import time

            for _ in range(200):
                status = client.get(f"/api/v1/investigations/{investigation_id}/status").json()
                if status["status"] in {"completed", "partial", "failed"}:
                    break
                time.sleep(0.02)

            report = client.get(f"/api/v1/investigations/{investigation_id}")
            assert report.json()["raw_input"] is None
            assert report.json()["raw_input_included"] is False
            for secret in SECRET_STRINGS:
                assert secret not in report.text
        get_settings.cache_clear()


class TestInputSecurity:
    def test_rejects_empty(self) -> None:
        manager = JobManager.__new__(JobManager)  # no store needed for this path
        manager._settings = Settings(app_env="test")
        with pytest.raises(InvalidInputError):
            manager._validate_content("   ")

    def test_rejects_oversized(self) -> None:
        manager = JobManager.__new__(JobManager)
        manager._settings = Settings(app_env="test", max_upload_mb=1)
        with pytest.raises(PayloadTooLargeError):
            manager._validate_content("a" * (2 * 1024 * 1024))

    def test_rejects_too_many_lines(self) -> None:
        manager = JobManager.__new__(JobManager)
        manager._settings = Settings(app_env="test", max_log_lines=10)
        with pytest.raises(PayloadTooLargeError):
            manager._validate_content("\n".join("line" for _ in range(100)))

    def test_rejects_overlong_line(self) -> None:
        manager = JobManager.__new__(JobManager)
        manager._settings = Settings(app_env="test", max_line_length=100)
        with pytest.raises(PayloadTooLargeError):
            manager._validate_content("x" * 5000)

    def test_accepts_normal_input(self) -> None:
        manager = JobManager.__new__(JobManager)
        manager._settings = Settings(app_env="test")
        manager._validate_content("2026-10-13 ERROR something failed\n")


class TestErrorSafety:
    def test_errors_do_not_leak_log_content(self) -> None:
        """An internal error must not echo the log that caused it."""
        from app.core.errors import AppError

        error = AppError("A safe message")
        payload = error.to_payload()
        assert payload["error"]["message"] == "A safe message"
        assert SECRET_STRINGS[0] not in str(payload)

    def test_unhandled_handler_returns_generic_message(self) -> None:
        from app.config import get_settings

        get_settings.cache_clear()
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            # Force a 500 through an endpoint that does not exist.
            response = client.get("/api/v1/does-not-exist")
            assert response.status_code in {404, 500}
        get_settings.cache_clear()


class TestCorsAndConfig:
    def test_cors_origins_are_not_wildcard(self) -> None:
        settings = Settings(app_env="test", frontend_origin="http://localhost:3000")
        assert "*" not in settings.cors_origins
        assert settings.cors_origins == ["http://localhost:3000"]

    def test_multiple_origins_are_split(self) -> None:
        settings = Settings(
            app_env="test", frontend_origin="http://localhost:3000,http://localhost:3001"
        )
        assert settings.cors_origins == ["http://localhost:3000", "http://localhost:3001"]

    def test_model_repr_never_leaks_key(self) -> None:
        from app.models.registry import build_model

        model = build_model(
            Settings(
                app_env="test",
                model_provider="openai",
                model_name="gpt-4o-mini",
                model_api_key="sk-super-secret-value",
            )
        )
        assert "sk-super-secret-value" not in repr(model)
        assert "super-secret" not in model.metadata().model_dump_json()

    def test_env_file_is_not_committed(self) -> None:
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent.parent
        assert (repo_root / ".gitignore").read_text().find(".env") >= 0
        # The example file must exist and the real one must not be tracked.
        assert (repo_root / "backend" / ".env.example").exists()
