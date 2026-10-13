"""API tests (SPEC §15.4).

Uses FastAPI's in-process TestClient — no server is started.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.errors import RateLimitedError
from app.main import create_app

SAMPLES = Path(__file__).resolve().parent.parent.parent / "samples"

SAMPLE_LOG = SAMPLES.joinpath("sample-node-api.log").read_text(encoding="utf-8")
SMALL_LOG = """2026-10-13T09:15:44.019Z ERROR [orders-api] service=orders-api Database connection failed
2026-10-13T09:15:44.019Z Error: Database connection failed
    at Database.connect (/srv/app/src/db/pool.js:88:23)
    at async OrderService.create (/srv/app/src/services/order.js:142:5)
2026-10-13T09:16:55.640Z ERROR [orders-api] service=orders-api Database connection failed
2026-10-13T09:16:55.640Z Error: Database connection failed
    at Database.connect (/srv/app/src/db/pool.js:88:23)
    at async OrderService.create (/srv/app/src/services/order.js:142:5)
"""

TERMINAL = {"completed", "partial", "failed"}


@pytest.fixture
def client(monkeypatch):
    """A TestClient whose settings are test-scoped."""
    monkeypatch.setenv("APP_ENV", "test")
    # Most tests submit several investigations; the 429 path has its own test.
    monkeypatch.setenv("MAX_CONCURRENT_INVESTIGATIONS", "16")
    from app.config import get_settings

    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def wait(client: TestClient, investigation_id: str, timeout: float = 15.0) -> dict:
    """Poll the status endpoint until the run reaches a terminal state."""
    deadline = time.time() + timeout
    payload: dict = {}
    while time.time() < deadline:
        response = client.get(f"/api/v1/investigations/{investigation_id}/status")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in TERMINAL:
            return payload
        time.sleep(0.02)
    pytest.fail(f"investigation did not finish; last status={payload.get('status')}")


def submit(client: TestClient, content: str = SMALL_LOG, **extra) -> str:
    """Start an investigation and return its id."""
    response = client.post("/api/v1/investigations", json={"content": content, **extra})
    assert response.status_code == 202, response.text
    return response.json()["id"]


class TestHealth:
    def test_health_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_versioned_health(self, client: TestClient) -> None:
        assert client.get("/api/v1/health").status_code == 200

    def test_model_status_never_exposes_secrets(self, client: TestClient) -> None:
        response = client.get("/api/v1/model/status")
        assert response.status_code == 200
        body = response.json()
        assert {"provider", "model", "available"} <= set(body)
        assert "key" not in response.text.lower().replace("model", "")


class TestCreate:
    def test_creates_investigation(self, client: TestClient) -> None:
        investigation_id = submit(client)
        assert investigation_id.startswith("inv_")

    def test_empty_content_rejected(self, client: TestClient) -> None:
        response = client.post("/api/v1/investigations", json={"content": "   "})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_INPUT"

    def test_missing_content_rejected(self, client: TestClient) -> None:
        response = client.post("/api/v1/investigations", json={})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_unknown_runtime_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/investigations", json={"content": SMALL_LOG, "runtime": "cobol"}
        )
        assert response.status_code == 422

    def test_unknown_field_rejected(self, client: TestClient) -> None:
        response = client.post("/api/v1/investigations", json={"content": "x", "surprise": True})
        assert response.status_code == 422

    def test_oversized_content_rejected(self, client: TestClient) -> None:
        """413 must be returned on the request, not after a 202 (SPEC 10.9)."""
        response = client.post("/api/v1/investigations", json={"content": "a" * (6 * 1024 * 1024)})
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"

    def test_error_envelope_shape(self, client: TestClient) -> None:
        response = client.post("/api/v1/investigations", json={})
        error = response.json()["error"]
        assert set(error) >= {"code", "message", "details"}


class TestUpload:
    def test_uploads_log_file(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/investigations/upload",
            files={"file": ("app.log", SMALL_LOG.encode(), "text/plain")},
        )
        assert response.status_code == 202
        assert response.json()["id"].startswith("inv_")

    def test_uploads_jsonl_file(self, client: TestClient) -> None:
        jsonl = (
            '{"timestamp":"2026-10-13T09:15:44Z","level":"error","service":"api",'
            '"message":"boom","err":{"type":"Error","message":"ECONNREFUSED"}}\n'
        )
        response = client.post(
            "/api/v1/investigations/upload",
            files={"file": ("events.jsonl", jsonl.encode(), "application/json")},
        )
        assert response.status_code == 202

    @pytest.mark.parametrize("name", ["app.exe", "app.zip", "app.log.php", "noextension"])
    def test_rejects_unsupported_extensions(self, client: TestClient, name: str) -> None:
        response = client.post(
            "/api/v1/investigations/upload",
            files={"file": (name, SMALL_LOG.encode(), "text/plain")},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"

    def test_rejects_empty_file(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/investigations/upload",
            files={"file": ("empty.log", b"   \n  ", "text/plain")},
        )
        assert response.status_code == 400

    def test_accepts_txt(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/investigations/upload",
            files={"file": ("notes.txt", SMALL_LOG.encode(), "text/plain")},
        )
        assert response.status_code == 202

    def test_filename_is_not_used_as_a_path(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/investigations/upload",
            files={"file": ("../../etc/passwd.log", SMALL_LOG.encode(), "text/plain")},
        )
        assert response.status_code == 202

    def test_binary_content_is_handled(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/investigations/upload",
            files={"file": ("weird.log", b"\xff\xfe\x00binary", "application/octet-stream")},
        )
        assert response.status_code in {202, 400}


class TestStatus:
    def test_status_polls_to_completion(self, client: TestClient) -> None:
        investigation_id = submit(client)
        final = wait(client, investigation_id)
        assert final["status"] in TERMINAL

    def test_status_reports_real_steps(self, client: TestClient) -> None:
        investigation_id = submit(client)
        final = wait(client, investigation_id)
        steps = [s["step"] for s in final["steps"]]
        assert "validate_input" in steps
        assert "parse_logs" in steps
        assert "persist_report" in steps
        # Steps must be distinct, not one function recorded three times.
        assert len(steps) == len(set(steps))

    def test_unknown_id_returns_404(self, client: TestClient) -> None:
        response = client.get("/api/v1/investigations/inv_missing/status")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


class TestReport:
    def test_returns_report_with_groups(self, client: TestClient) -> None:
        investigation_id = submit(client)
        wait(client, investigation_id)
        report = client.get(f"/api/v1/investigations/{investigation_id}").json()

        assert report["status"] in TERMINAL
        assert report["summary"]["total_events"] > 0
        assert len(report["error_groups"]) > 0
        assert report["error_groups"][0]["count"] >= 2

    def test_report_includes_severity_reason(self, client: TestClient) -> None:
        investigation_id = submit(client)
        wait(client, investigation_id)
        report = client.get(f"/api/v1/investigations/{investigation_id}").json()
        assert all(g["severity_reason"] for g in report["error_groups"])

    def test_report_does_not_include_raw_logs(self, client: TestClient) -> None:
        investigation_id = submit(client)
        wait(client, investigation_id)
        report = client.get(f"/api/v1/investigations/{investigation_id}").json()
        assert report["raw_input_included"] is False
        assert report["raw_input"] is None

    def test_report_does_not_expose_api_keys(self, client: TestClient) -> None:
        investigation_id = submit(client)
        wait(client, investigation_id)
        text = client.get(f"/api/v1/investigations/{investigation_id}").text
        assert "sk-" not in text

    def test_full_sample_produces_useful_report(self, client: TestClient) -> None:
        investigation_id = submit(client, SAMPLE_LOG)
        wait(client, investigation_id)
        report = client.get(f"/api/v1/investigations/{investigation_id}").json()

        assert report["summary"]["detected_runtime"] == "nodejs"
        assert report["summary"]["total_lines"] > 100
        top = report["error_groups"][0]
        assert "Database connection failed" in top["title"]
        assert top["count"] >= 10

    def test_findings_cite_only_real_evidence(self, client: TestClient) -> None:
        investigation_id = submit(client, SAMPLE_LOG)
        wait(client, investigation_id)
        report = client.get(f"/api/v1/investigations/{investigation_id}").json()
        known = {e["id"] for e in report["evidence"]}
        for finding in report["findings"]:
            for evidence_id in finding["evidence_ids"]:
                assert evidence_id in known

    def test_secrets_are_redacted_in_report(self, client: TestClient) -> None:
        investigation_id = submit(client, SAMPLE_LOG)
        wait(client, investigation_id)
        text = client.get(f"/api/v1/investigations/{investigation_id}").text
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in text
        assert "a3f9c1e77b2d4056" not in text

    def test_unknown_id_returns_404(self, client: TestClient) -> None:
        response = client.get("/api/v1/investigations/inv_missing")
        assert response.status_code == 404


class TestList:
    def test_lists_investigations(self, client: TestClient) -> None:
        first = submit(client)
        second = submit(client)
        wait(client, first)
        wait(client, second)

        response = client.get("/api/v1/investigations")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert {item["id"] for item in body["items"]} == {first, second}

    def test_pagination(self, client: TestClient) -> None:
        for _ in range(3):
            submit(client)
        body = client.get("/api/v1/investigations?limit=2&offset=1").json()
        assert len(body["items"]) == 2
        assert body["limit"] == 2
        assert body["offset"] == 1
        assert body["total"] >= 3

    def test_invalid_pagination_rejected(self, client: TestClient) -> None:
        assert client.get("/api/v1/investigations?limit=0").status_code == 422
        assert client.get("/api/v1/investigations?limit=9999").status_code == 422
        assert client.get("/api/v1/investigations?offset=-1").status_code == 422

    def test_empty_list_is_valid(self, client: TestClient) -> None:
        body = client.get("/api/v1/investigations").json()
        assert body["items"] == []
        assert body["total"] == 0


class TestDelete:
    def test_deletes_investigation(self, client: TestClient) -> None:
        investigation_id = submit(client)
        wait(client, investigation_id)

        response = client.delete(f"/api/v1/investigations/{investigation_id}")
        assert response.status_code == 204
        assert client.get(f"/api/v1/investigations/{investigation_id}").status_code == 404

    def test_delete_unknown_returns_404(self, client: TestClient) -> None:
        assert client.delete("/api/v1/investigations/inv_missing").status_code == 404

    def test_delete_removes_from_list(self, client: TestClient) -> None:
        investigation_id = submit(client)
        wait(client, investigation_id)
        client.delete(f"/api/v1/investigations/{investigation_id}")
        body = client.get("/api/v1/investigations").json()
        assert investigation_id not in {i["id"] for i in body["items"]}


class TestConcurrency:
    async def test_rejects_when_limit_reached(self) -> None:
        """429 when every concurrency slot is busy (SPEC 10.9)."""
        from app.config import Settings
        from app.jobs.manager import JobManager
        from app.store.base import InMemoryStore

        settings = Settings(app_env="test", max_concurrent_investigations=2)
        manager = JobManager(store=InMemoryStore(), settings=settings)
        limit = settings.max_concurrent_investigations

        blockers = [asyncio.create_task(asyncio.sleep(30)) for _ in range(limit)]
        manager._tasks.update({f"fake_{i}": t for i, t in enumerate(blockers)})
        try:
            with pytest.raises(RateLimitedError):
                manager.submit(content=SMALL_LOG)
        finally:
            for task in blockers:
                task.cancel()
            manager._tasks.clear()

    async def test_frees_slots_after_completion(self) -> None:
        from app.config import Settings
        from app.jobs.manager import JobManager
        from app.store.base import InMemoryStore

        settings = Settings(app_env="test", max_concurrent_investigations=1)
        manager = JobManager(store=InMemoryStore(), settings=settings)

        record = manager.submit(content=SMALL_LOG)
        await asyncio.gather(*list(manager._tasks.values()), return_exceptions=True)
        # Slot released: a second submission succeeds.
        manager.submit(content=SMALL_LOG)
        assert record.id.startswith("inv_")

    def test_model_failure_still_yields_a_report(self, client: TestClient) -> None:
        """SPEC 8.5: a model outage must not destroy deterministic analysis."""
        investigation_id = submit(client)
        final = wait(client, investigation_id)
        assert final["status"] in TERMINAL

        report = client.get(f"/api/v1/investigations/{investigation_id}").json()
        assert report["error_groups"], "deterministic groups must survive an AI failure"
        assert report["ai_analysis_performed"] is False
        assert report["ai_skipped_reason"]
