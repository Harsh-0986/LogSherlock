"""End-to-end deterministic pipeline tests (SPEC §15.6).

These run with no model connection whatsoever.
"""

from __future__ import annotations

from app.logengine.models import Runtime
from app.logengine.pipeline import analyze
from app.models.base import ModelUnavailableError


class TestPipelineDeterministic:
    def test_node_sample_groups_correctly(self, settings, sample_node_log: str) -> None:
        result = analyze(text=sample_node_log, settings=settings)

        assert result.summary.detected_runtime is Runtime.NODEJS
        assert result.summary.total_lines > 100
        assert result.summary.error_group_count > 0

        # The dominant repeated error must be the database connection failure.
        top = result.groups[0]
        assert "Database connection failed" in top.title
        assert top.count >= 10

    def test_group_counts_are_accurate(self, settings, sample_node_log: str) -> None:
        result = analyze(text=sample_node_log, settings=settings)
        for group in result.groups:
            # Every counted occurrence maps to a real parsed event.
            for line_no in group.line_numbers:
                assert any(e.line_number == line_no for e in result.events)

    def test_is_reproducible(self, settings, sample_node_log: str) -> None:
        first = analyze(text=sample_node_log, settings=settings)
        second = analyze(text=sample_node_log, settings=settings)
        assert [g.fingerprint for g in first.groups] == [g.fingerprint for g in second.groups]
        assert [g.count for g in first.groups] == [g.count for g in second.groups]

    def test_python_sample_groups_correctly(self, settings, sample_python_log: str) -> None:
        result = analyze(text=sample_python_log, settings=settings)
        assert result.summary.detected_runtime is Runtime.PYTHON
        top = result.groups[0]
        # Orders differ only by id, so all 12 tracebacks must collapse to one
        # group identified by the exception, not by the varying order number.
        assert top.exception_type == "ConnectionError"
        assert "could not connect" in top.title
        assert top.count >= 10

    def test_evidence_ids_resolve_to_real_events(self, settings, sample_node_log: str) -> None:
        result = analyze(text=sample_node_log, settings=settings)
        known = {e.evidence_id for e in result.events}
        for item in result.evidence.items:
            assert item.id in known

    def test_evidence_is_redacted_before_model(self, settings, sample_node_log: str) -> None:
        result = analyze(text=sample_node_log, settings=settings)
        blob = " ".join(i.text for i in result.evidence.items)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in blob
        assert "a3f9c1e77b2d4056" not in blob

    def test_evidence_respects_char_budget(self, sample_node_log: str) -> None:
        from app.config import Settings

        tight = Settings(app_env="test", max_evidence_chars=2000)
        result = analyze(text=sample_node_log, settings=tight)
        assert result.evidence.total_chars <= 2000 + 2000  # budget plus last-item slack
        assert result.evidence.truncated

    def test_truncation_is_disclosed(self, sample_node_log: str) -> None:
        from app.config import Settings

        tight = Settings(app_env="test", max_evidence_chars=800)
        result = analyze(text=sample_node_log, settings=tight)
        assert result.evidence.note
        assert any("truncat" in w.lower() for w in result.warnings)

    def test_secrets_never_survive_in_events(self, settings, sample_node_log: str) -> None:
        result = analyze(text=sample_node_log, settings=settings)
        for event in result.events:
            assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in event.message
            assert "a3f9c1e77b2d4056" not in event.message

    def test_prompt_injection_is_carried_as_data(self, settings, sample_node_log: str) -> None:
        result = analyze(text=sample_node_log, settings=settings)
        # The injection text must survive as an ordinary log line, not be
        # acted on. It must not become the top finding.
        blob = " ".join(e.message for e in result.events)
        assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in blob
        assert "IGNORE ALL PREVIOUS" not in result.groups[0].title

    def test_empty_input_is_rejected_by_caller(self, settings) -> None:
        # The pipeline itself tolerates empty input; the API layer rejects it.
        result = analyze(text="", settings=settings)
        assert result.summary.total_events == 0
        assert result.groups == []

    def test_unrecognised_format_discloses_limitation(self, settings) -> None:
        text = "banana\napple\ncherry\n" * 5
        result = analyze(text=text, settings=settings)
        assert any("not recognised" in w for w in result.warnings)

    def test_runtime_can_be_forced(self, settings, sample_node_log: str) -> None:
        result = analyze(text=sample_node_log, settings=settings, runtime=Runtime.GENERIC)
        assert result.summary.detected_runtime is Runtime.GENERIC

    def test_no_errors_found_is_not_a_failure(self, settings) -> None:
        text = "2026-10-13T09:00:00Z INFO  [api] service=api everything is fine\n"
        result = analyze(text=text, settings=settings)
        assert result.groups == []
        assert result.summary.total_events == 1


class TestUserFacingOutput:
    """Guards on what the report actually shows a user."""

    def test_severity_reason_never_leaks_a_regex(self, settings, sample_node_log: str) -> None:
        """The severity reason is prose, not a developer-facing pattern."""
        import re

        result = analyze(text=sample_node_log, settings=settings)
        for group in result.groups:
            reason = group.severity_reason
            assert "(?i)" not in reason, reason
            assert "\\b" not in reason, reason
            assert "(?:" not in reason, reason
            assert not re.search(r"[\\^$|{}]", reason), reason

    def test_line_count_is_consistent(self, settings, sample_node_log: str) -> None:
        """A trailing newline must not make the report disagree with itself."""
        from app.logengine.pipeline import count_lines

        result = analyze(text=sample_node_log, settings=settings)
        assert result.summary.total_lines == count_lines(sample_node_log)

    def test_trailing_newline_adds_no_phantom_line(self) -> None:
        """A file ending in a newline has N lines, not N+1."""
        from app.logengine.pipeline import count_lines

        assert count_lines("a\nb\nc\n") == 3
        assert count_lines("a\nb\nc") == 3

    def test_step_summary_agrees_with_report(self, settings, sample_node_log: str) -> None:
        """The validate_input step and the report must report the same number."""
        import asyncio

        from fake_model import FakeModel

        from app.config import Settings as _Settings
        from app.graph.builder import run_investigation

        async def run() -> dict:
            return await run_investigation(
                FakeModel(error=ModelUnavailableError("offline")),
                investigation_id="inv_x",
                raw_input=sample_node_log,
                settings=_Settings(app_env="test"),
            )

        state = asyncio.run(run())
        step = next(s for s in state["processing_steps"] if s["step"] == "validate_input")
        assert f"{state['summary'].total_lines} lines" in step["summary"]
