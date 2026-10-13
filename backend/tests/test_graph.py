"""AI workflow tests with a mocked model (SPEC §15.3).

No test here contacts a live model.
"""

from __future__ import annotations

import json

import pytest
from fake_model import FakeModel, make_report
from pydantic import ValidationError

from app.config import Settings
from app.graph.builder import run_investigation
from app.graph.schemas import Finding, ModelReport
from app.graph.validation import validate_report
from app.logengine.pipeline import analyze
from app.models.base import ModelTimeoutError, ModelUnavailableError

SAMPLE = """2026-10-13T09:15:44.019Z ERROR [orders-api] service=orders-api Database connection failed
2026-10-13T09:15:44.019Z Error: Database connection failed
    at Database.connect (/srv/app/src/db/pool.js:88:23)
    at async OrderService.create (/srv/app/src/services/order.js:142:5)
2026-10-13T09:16:55.640Z ERROR [orders-api] service=orders-api Database connection failed
2026-10-13T09:16:55.640Z Error: Database connection failed
    at Database.connect (/srv/app/src/db/pool.js:88:23)
    at async OrderService.create (/srv/app/src/services/order.js:142:5)
2026-10-13T09:17:02.113Z ERROR [notification-svc] service=notification-svc SMTP relay returned 421
"""


@pytest.fixture
def deterministic():
    """Run the deterministic half so tests can read real fingerprints."""
    return analyze(text=SAMPLE, settings=Settings(app_env="test"))


async def run_graph(settings: Settings, model: FakeModel, text: str = SAMPLE, **kw):
    """Execute the compiled graph over ``text``."""
    return await run_investigation(
        model,
        investigation_id="inv_test",
        raw_input=text,
        settings=settings,
        **kw,
    )


class TestSchemaContract:
    def test_valid_report_parses(self) -> None:
        report = ModelReport.model_validate(
            json.loads(make_report(fingerprint="abc123", evidence_ids=["line-1"]))
        )
        assert report.findings[0].confidence == "medium"
        assert report.findings[0].recommended_steps[0].action

    def test_invalid_confidence_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelReport.model_validate(
                {
                    "summary": "x",
                    "findings": [
                        {
                            "fingerprint": "a",
                            "title": "t",
                            "likely_cause": "c",
                            "confidence": "extremely-high",
                        }
                    ],
                }
            )

    def test_missing_required_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelReport.model_validate({"findings": [{"title": "no cause"}]})


class TestValidation:
    def test_accepts_valid_findings(self, deterministic) -> None:
        ids = {i.id for i in deterministic.evidence.items}
        group = deterministic.groups[0]
        raw = json.loads(make_report(fingerprint=group.fingerprint, evidence_ids=sorted(ids)[:2]))
        outcome = validate_report(raw, groups=deterministic.groups, valid_evidence_ids=ids)
        assert outcome.ok
        assert outcome.usable

    def test_rejects_fabricated_evidence_ids(self, deterministic) -> None:
        group = deterministic.groups[0]
        raw = json.loads(make_report(fingerprint=group.fingerprint, evidence_ids=["line-999999"]))
        ids = {i.id for i in deterministic.evidence.items}
        outcome = validate_report(raw, groups=deterministic.groups, valid_evidence_ids=ids)
        assert not outcome.ok
        assert any("never supplied" in e for e in outcome.errors)

    def test_rejects_fabricated_fingerprint(self, deterministic) -> None:
        ids = {i.id for i in deterministic.evidence.items}
        raw = json.loads(make_report(fingerprint="deadbeefdeadbeef", evidence_ids=sorted(ids)[:1]))
        outcome = validate_report(raw, groups=deterministic.groups, valid_evidence_ids=ids)
        assert not outcome.ok
        assert any("unknown fingerprint" in e for e in outcome.errors)

    def test_rejects_claim_with_no_evidence(self, deterministic) -> None:
        group = deterministic.groups[0]
        raw = json.loads(make_report(fingerprint=group.fingerprint, evidence_ids=[]))
        ids = {i.id for i in deterministic.evidence.items}
        outcome = validate_report(raw, groups=deterministic.groups, valid_evidence_ids=ids)
        assert any("cites no evidence" in e for e in outcome.errors)

    def test_deterministic_severity_wins(self, deterministic) -> None:
        """The model may not overwrite the computed severity (SPEC 7.7)."""
        ids = {i.id for i in deterministic.evidence.items}
        group = deterministic.groups[0]
        raw = json.loads(
            make_report(
                fingerprint=group.fingerprint,
                evidence_ids=sorted(ids)[:2],
                severity="info",
            )
        )
        outcome = validate_report(raw, groups=deterministic.groups, valid_evidence_ids=ids)
        assert outcome.findings[0].severity == group.severity.value

    def test_malformed_structure_reports_error(self, deterministic) -> None:
        ids = {i.id for i in deterministic.evidence.items}
        outcome = validate_report(
            {"nonsense": True}, groups=deterministic.groups, valid_evidence_ids=ids
        )
        assert not outcome.ok


class TestGraphExecution:
    async def test_valid_output_completes(self, settings, deterministic) -> None:
        ids = sorted(i.id for i in deterministic.evidence.items)
        model = FakeModel(
            [make_report(fingerprint=deterministic.groups[0].fingerprint, evidence_ids=ids[:2])]
        )
        state = await run_graph(settings, model)
        assert state["status"] == "completed"
        assert len(state["findings"]) == 1

    async def test_invalid_json_triggers_bounded_repair(self, settings, deterministic) -> None:
        ids = sorted(i.id for i in deterministic.evidence.items)
        good = make_report(fingerprint=deterministic.groups[0].fingerprint, evidence_ids=ids[:2])
        model = FakeModel(["this is not json at all", good])
        state = await run_graph(settings, model)
        assert state["repair_attempted"] is True
        assert state["status"] == "completed"
        assert len(state["findings"]) == 1
        assert len(model.calls) == 2

    async def test_repair_is_bounded_to_one_attempt(self, settings) -> None:
        model = FakeModel(["still not json", "still not json", "still not json"])
        state = await run_graph(settings, model)
        assert state["repair_attempted"] is True
        # One initial call + at most one repair.
        assert len(model.calls) <= 2

    async def test_fabricated_evidence_is_repaired_then_accepted(
        self, settings, deterministic
    ) -> None:
        ids = sorted(i.id for i in deterministic.evidence.items)
        bad = make_report(
            fingerprint=deterministic.groups[0].fingerprint, evidence_ids=["line-999999"]
        )
        good = make_report(fingerprint=deterministic.groups[0].fingerprint, evidence_ids=ids[:2])
        model = FakeModel([bad, good])
        state = await run_graph(settings, model)
        assert len(state["findings"]) == 1
        assert all(e in ids for e in state["findings"][0]["evidence_ids"])

    async def test_model_unavailable_falls_back(self, settings) -> None:
        model = FakeModel(error=ModelUnavailableError("connection refused"))
        state = await run_graph(settings, model)
        assert state["status"] == "partial"
        assert state["findings"] == []
        assert state["ai_skipped_reason"]

    async def test_model_timeout_falls_back(self, settings) -> None:
        model = FakeModel(error=ModelTimeoutError("timed out after 90s"))
        state = await run_graph(settings, model)
        assert state["status"] == "partial"
        assert "timed out" in (state["ai_skipped_reason"] or "")

    async def test_permanently_invalid_output_falls_back(self, settings, deterministic) -> None:
        model = FakeModel(["garbage", "more garbage"])
        state = await run_graph(settings, model)
        assert state["status"] == "partial"
        assert state["validation_errors"]

    async def test_deterministic_groups_survive_ai_failure(self, settings) -> None:
        model = FakeModel(error=ModelUnavailableError("down"))
        state = await run_graph(settings, model)
        assert len(state["groups"]) > 0

    async def test_empty_input_is_rejected_before_the_graph(self, settings) -> None:
        from app.core.errors import InvalidInputError

        model = FakeModel(["{}"])
        with pytest.raises(InvalidInputError):
            await run_graph(settings, model, text="   \n  ")

    async def test_oversized_input_is_rejected(self) -> None:
        from app.core.errors import PayloadTooLargeError

        settings = Settings(app_env="test", max_log_lines=5)
        model = FakeModel(["{}"])
        with pytest.raises(PayloadTooLargeError):
            await run_graph(settings, model, text="\n".join(f"line {i}" for i in range(50)))

    async def test_no_errors_found_is_a_success(self, settings) -> None:
        text = "2026-10-13T09:00:00Z INFO  [api] service=api everything fine\n"
        model = FakeModel(["{}"])
        state = await run_graph(settings, model, text=text)
        assert state["status"] == "partial"  # AI skipped, nothing to investigate
        assert state["groups"] == []

    async def test_records_every_step(self, settings, deterministic) -> None:
        ids = sorted(i.id for i in deterministic.evidence.items)
        model = FakeModel(
            [make_report(fingerprint=deterministic.groups[0].fingerprint, evidence_ids=ids[:2])]
        )
        state = await run_graph(settings, model)
        steps = [s["step"] for s in state["processing_steps"]]
        assert steps[0] == "validate_input"
        assert "persist_report" in steps
        assert all(s["started_at"] for s in state["processing_steps"])

    async def test_status_endpoint_fields_present(self, settings) -> None:
        model = FakeModel(error=ModelUnavailableError("down"))
        state = await run_graph(settings, model)
        assert state["status"] in {"completed", "partial", "failed"}
        assert state["started_at"]


class TestPromptSafety:
    async def test_prompt_marks_evidence_as_untrusted(self, settings, deterministic) -> None:
        ids = sorted(i.id for i in deterministic.evidence.items)
        model = FakeModel(
            [make_report(fingerprint=deterministic.groups[0].fingerprint, evidence_ids=ids[:2])]
        )
        await run_graph(settings, model)
        assert "untrusted" in model.system_prompt.lower()
        assert "never follow" in model.system_prompt.lower()

    async def test_prompt_contains_evidence_ids(self, settings, deterministic) -> None:
        model = FakeModel(
            [make_report(fingerprint=deterministic.groups[0].fingerprint, evidence_ids=["line-1"])]
        )
        await run_graph(settings, model)
        for item in deterministic.evidence.items[:3]:
            assert item.id in model.last_prompt

    async def test_model_is_told_valid_evidence_ids_only(self, settings, deterministic) -> None:
        model = FakeModel(["{}"])
        await run_graph(settings, model)
        # Fabricated ids must not be advertised in the prompt.
        assert "line-999999" not in model.last_prompt

    async def test_full_log_is_not_sent_verbatim(self, settings) -> None:
        huge = "\n".join(
            f"2026-10-13T09:00:00Z INFO  [api] service=api filler line {i}" for i in range(400)
        )
        model = FakeModel(["{}"])
        await run_graph(settings, model, text=huge)
        # Evidence is bounded, so the prompt must be far smaller than the input.
        assert len(model.last_prompt) < len(huge) / 2


def test_finding_model_copy_preserves_citations() -> None:
    f = Finding(fingerprint="a", title="t", likely_cause="c", evidence_ids=["line-1"])
    copied = f.model_copy(update={"confidence": "high"})
    assert copied.evidence_ids == ["line-1"]
    assert copied.confidence == "high"
