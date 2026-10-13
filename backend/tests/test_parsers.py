"""Parser tests (SPEC §15.1)."""

from __future__ import annotations

import pytest
from fixtures.log_samples import (
    DOTNET_CORE,
    EMPTY,
    JSONL_MIXED,
    LONG_LINE,
    MALFORMED_TIMESTAMP,
    NO_TIMESTAMP,
    NODE_PINO,
    NODE_PLAIN,
    PYTHON_CHAINED,
    PYTHON_COLON_FORMAT,
    PYTHON_TRACEBACK,
)

from app.logengine.models import LogLevel
from app.logengine.parsers.dotnet_logs import DotNetParser
from app.logengine.parsers.generic import GenericParser
from app.logengine.parsers.jsonl import JsonlParser
from app.logengine.parsers.nodejs_logs import NodeParser
from app.logengine.parsers.python_logs import PythonParser


class TestPythonParser:
    def test_extracts_traceback_and_exception(self) -> None:
        events = PythonParser().parse(PYTHON_TRACEBACK)
        assert len(events) == 1
        event = events[0]
        assert event.exception_type == "ConnectionError"
        assert event.exception_message == "could not connect to server"
        assert event.level is LogLevel.ERROR
        assert event.timestamp is not None
        assert "Traceback" in (event.stack_trace or "")

    def test_line_numbers_cover_the_whole_traceback(self) -> None:
        event = PythonParser().parse(PYTHON_TRACEBACK)[0]
        assert event.line_number == 1
        assert event.end_line_number == 7
        assert event.span == 7

    def test_chained_exception_uses_outermost(self) -> None:
        event = PythonParser().parse(PYTHON_CHAINED)[0]
        assert event.exception_type == "RuntimeError"
        assert "could not publish order event" in (event.exception_message or "")
        # The inner cause is preserved in the raw text.
        assert "OperationalError" in event.raw_text

    def test_colon_format(self) -> None:
        events = PythonParser().parse(PYTHON_COLON_FORMAT)
        assert len(events) == 3
        assert events[1].logger == "shopstream.orders"
        assert events[1].level is LogLevel.ERROR

    def test_detects_python(self) -> None:
        assert PythonParser().can_parse(PYTHON_TRACEBACK)
        assert not PythonParser().can_parse(NODE_PLAIN)


class TestNodeParser:
    def test_extracts_error_and_stack(self) -> None:
        events = NodeParser().parse(NODE_PLAIN)
        errors = [e for e in events if e.exception_type]
        assert len(errors) == 2
        assert errors[0].exception_type == "Error"
        assert errors[0].exception_message == "Database connection failed"
        assert "at Database.connect" in (errors[0].stack_trace or "")

    def test_pino_structured_record(self) -> None:
        events = NodeParser().parse(NODE_PINO)
        assert len(events) == 2
        error = events[0]
        assert error.level is LogLevel.ERROR
        assert error.service == "orders-api"
        assert error.message == "request failed"
        assert error.exception_type == "Error"
        assert error.timestamp is not None
        assert events[1].level is LogLevel.INFO

    def test_detects_node(self) -> None:
        assert NodeParser().can_parse(NODE_PLAIN)
        assert not NodeParser().can_parse(PYTHON_TRACEBACK)


class TestDotNetParser:
    def test_extracts_exception_and_frames(self) -> None:
        events = DotNetParser().parse(DOTNET_CORE)
        event = events[0]
        assert event.exception_type == "System.Data.SqlClient.SqlException"
        assert event.exception_message == "Connection refused"
        assert event.level is LogLevel.ERROR
        assert "ShopStream.Data.Pool.SqlPool.CreateConnection" in (event.stack_trace or "")

    def test_detects_dotnet(self) -> None:
        assert DotNetParser().can_parse(DOTNET_CORE)


class TestJsonlParser:
    def test_parses_records(self) -> None:
        events = JsonlParser().parse(JSONL_MIXED)
        assert len(events) == 3
        assert events[0].exception_type == "Error"
        assert events[0].service == "orders-api"
        assert events[1].level is LogLevel.INFO

    def test_malformed_record_degrades_safely(self) -> None:
        events = JsonlParser().parse(JSONL_MIXED)
        malformed = events[2]
        assert malformed.warnings  # carries a warning, does not raise
        assert "not valid json" in malformed.message

    def test_empty_input(self) -> None:
        assert JsonlParser().parse(EMPTY) == []


class TestGenericParser:
    def test_missing_timestamps_yield_none(self) -> None:
        events = GenericParser().parse(NO_TIMESTAMP)
        assert len(events) == 2
        assert all(e.timestamp is None for e in events)
        assert events[1].level is LogLevel.ERROR

    def test_empty_input(self) -> None:
        assert GenericParser().parse(EMPTY) == []

    def test_very_long_line_is_accepted(self) -> None:
        events = GenericParser().parse(f"ERROR {LONG_LINE}")
        assert len(events) == 1
        assert events[0].level is LogLevel.ERROR

    def test_unrecognised_format_still_parses(self) -> None:
        events = GenericParser().parse(MALFORMED_TIMESTAMP)
        assert len(events) == 2

    def test_never_invents_metadata(self) -> None:
        events = GenericParser().parse(MALFORMED_TIMESTAMP)
        assert all(e.service is None for e in events)
        assert all(e.exception_type is None for e in events)


@pytest.mark.parametrize(
    "parser",
    [PythonParser(), NodeParser(), DotNetParser(), JsonlParser(), GenericParser()],
)
def test_all_parsers_handle_empty_input(parser) -> None:
    assert parser.parse(EMPTY) == []
