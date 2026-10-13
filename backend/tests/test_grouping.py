"""Normalization and grouping tests (SPEC §15.2)."""

from __future__ import annotations

from fixtures.log_samples import (
    NODE_PLAIN,
    PROMPT_INJECTION,
    PYTHON_TRACEBACK,
)

from app.logengine.detect import detect_runtime
from app.logengine.fingerprint import compute_fingerprint, extract_frames
from app.logengine.grouping import group_events
from app.logengine.models import LogLevel, Runtime, Severity
from app.logengine.normalize import normalize_message
from app.logengine.parsers.nodejs_logs import NodeParser
from app.logengine.parsers.python_logs import PythonParser
from app.logengine.severity import rank_groups


class TestNormalization:
    def test_replaces_timestamps(self) -> None:
        a = normalize_message("Failed at 2026-10-13T09:15:44.019Z")
        b = normalize_message("Failed at 2026-10-13T11:02:01.774Z")
        assert a == b == "Failed at <TS>"

    def test_replaces_request_ids(self) -> None:
        a = normalize_message("GET /v1/orders 200 request_id=req_8f2a1c94d7")
        b = normalize_message("GET /v1/orders 200 request_id=req_1b93ee0a52")
        assert a == b

    def test_replaces_uuids(self) -> None:
        a = normalize_message("order 3f2a1c94-d7b2-4e8a-9c1d-5e6f7a8b9c0d failed")
        b = normalize_message("order 8a1b2c3d-4e5f-6a7b-8c9d-0e1f2a3b4c5d failed")
        assert a == b
        assert "<UUID>" in a

    def test_replaces_durations_and_ips(self) -> None:
        assert normalize_message("Slow query 1840ms") == normalize_message("Slow query 2210ms")
        assert normalize_message("host 10.42.7.19 up") == normalize_message("host 10.42.9.31 up")

    def test_preserves_http_status(self) -> None:
        assert "503" in normalize_message("POST /v1/orders status=503 circuit open")
        assert "404" in normalize_message("HTTP/1.1 404 Not Found")

    def test_preserves_sqlstate(self) -> None:
        out = normalize_message("FATAL: terminating connection due to SQLSTATE[08006]")
        assert "08006" in out

    def test_preserves_db_error_code(self) -> None:
        assert "ORA-01722" in normalize_message("ORA-01722: invalid number")

    def test_does_not_group_unrelated_errors(self) -> None:
        a = normalize_message("Database connection failed")
        b = normalize_message("Payment gateway timeout")
        assert a != b


class TestFingerprinting:
    def test_same_error_same_fingerprint(self) -> None:
        a = compute_fingerprint(exception_type="Error", normalized_message="db down", frames=())
        b = compute_fingerprint(exception_type="Error", normalized_message="db down", frames=())
        assert a == b

    def test_different_exception_types_stay_distinct(self) -> None:
        a = compute_fingerprint(exception_type="TypeError", normalized_message="x", frames=())
        b = compute_fingerprint(exception_type="RangeError", normalized_message="x", frames=())
        assert a != b

    def test_different_messages_stay_distinct(self) -> None:
        a = compute_fingerprint(exception_type="Error", normalized_message="db down")
        b = compute_fingerprint(exception_type="Error", normalized_message="disk full")
        assert a != b

    def test_is_deterministic_across_calls(self) -> None:
        kwargs = {
            "exception_type": "ConnectionError",
            "normalized_message": "could not connect",
            "frames": extract_frames('  File "/srv/a.py", line 44, in go'),
        }
        assert compute_fingerprint(**kwargs) == compute_fingerprint(**kwargs)

    def test_extracts_python_frames(self) -> None:
        frames = extract_frames(PYTHON_TRACEBACK)
        assert frames
        assert all(f.startswith("py:") for f in frames)

    def test_line_numbers_do_not_split_frames(self) -> None:
        a = extract_frames('  File "/srv/a.py", line 44, in go')
        b = extract_frames('  File "/srv/a.py", line 99, in go')
        assert a == b


class TestGrouping:
    def test_repeated_errors_form_one_group(self) -> None:
        groups = group_events(NodeParser().parse(NODE_PLAIN))
        assert len(groups) == 1
        assert groups[0].count == 2

    def test_variable_request_ids_do_not_fragment(self) -> None:
        text = (
            "2026-10-13T09:00:00Z ERROR [api] service=api boom request_id=req_aaa11111\n"
            "2026-10-13T09:00:01Z ERROR [api] service=api boom request_id=req_bbb22222\n"
            "2026-10-13T09:00:02Z ERROR [api] service=api boom request_id=req_ccc33333\n"
        )
        groups = group_events(NodeParser().parse(text))
        assert len(groups) == 1
        assert groups[0].count == 3

    def test_distinct_errors_stay_separate(self) -> None:
        text = (
            "2026-10-13T09:00:00Z ERROR [api] service=api Database connection failed\n"
            "2026-10-13T09:00:01Z ERROR [api] service=api Payment gateway timeout\n"
            "2026-10-13T09:00:02Z ERROR [api] service=api Disk quota exceeded\n"
        )
        groups = group_events(NodeParser().parse(text))
        assert len(groups) == 3

    def test_group_records_accurate_line_numbers(self) -> None:
        groups = group_events(NodeParser().parse(NODE_PLAIN))
        assert groups[0].line_numbers == (1, 5)

    def test_evidence_ids_match_line_numbers(self) -> None:
        groups = group_events(NodeParser().parse(NODE_PLAIN))
        group = groups[0]
        for evidence_id, line_no in zip(group.evidence_ids, group.line_numbers, strict=True):
            assert evidence_id == f"line-{line_no}"

    def test_info_events_are_not_error_groups(self) -> None:
        text = (
            "2026-10-13T09:00:00Z INFO  [api] service=api all good\n"
            "2026-10-13T09:00:01Z DEBUG [api] service=api verbose detail\n"
        )
        assert group_events(NodeParser().parse(text)) == []

    def test_prompt_injection_line_does_not_corrupt_grouping(self) -> None:
        groups = group_events(NodeParser().parse(PROMPT_INJECTION))
        db_groups = [g for g in groups if "Database connection failed" in g.title]
        assert len(db_groups) == 1
        # The injection note must remain its own event, never merged into the error.
        assert all("IGNORE ALL PREVIOUS" not in g.title for g in db_groups)

    def test_python_repeated_tracebacks_group(self) -> None:
        text = PYTHON_TRACEBACK + PYTHON_TRACEBACK
        groups = group_events(PythonParser().parse(text))
        assert len(groups) == 1
        assert groups[0].count == 2
        assert groups[0].exception_type == "ConnectionError"


class TestSeverity:
    def test_recurring_db_failure_is_high(self) -> None:
        groups = rank_groups(group_events(NodeParser().parse(NODE_PLAIN)))
        assert groups[0].severity is Severity.HIGH

    def test_severity_has_a_reason(self) -> None:
        groups = group_events(NodeParser().parse(NODE_PLAIN))
        assert groups[0].severity_reason

    def test_warnings_are_low(self) -> None:
        text = "2026-10-13T09:00:00Z WARN  [api] service=api slow query 1200ms\n"
        groups = rank_groups(group_events(NodeParser().parse(text)))
        assert groups[0].severity is Severity.LOW

    def test_critical_patterns_rank_critical(self) -> None:
        text = (
            "2026-10-13T09:00:00Z FATAL [api] service=api "
            "Out of memory: JavaScript heap out of memory\n"
        )
        groups = rank_groups(group_events(NodeParser().parse(text)))
        assert groups[0].severity is Severity.CRITICAL

    def test_ranking_is_deterministic(self) -> None:
        events = NodeParser().parse(NODE_PLAIN)
        first = [g.fingerprint for g in rank_groups(group_events(events))]
        second = [g.fingerprint for g in rank_groups(group_events(list(reversed(events))))]
        assert first == second


class TestDetection:
    def test_detects_python(self) -> None:
        assert detect_runtime(PYTHON_TRACEBACK) is Runtime.PYTHON

    def test_detects_node(self) -> None:
        assert detect_runtime(NODE_PLAIN) is Runtime.NODEJS

    def test_unknown_falls_back_to_generic(self) -> None:
        assert detect_runtime("just some words\nnothing structured here\n") is Runtime.GENERIC


def test_grouped_events_preserve_levels() -> None:
    events = NodeParser().parse(NODE_PLAIN)
    assert all(e.level is LogLevel.ERROR for e in events)
