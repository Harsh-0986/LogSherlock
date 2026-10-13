"""Format detection (SPEC §7.3).

Chooses a parser for the input. Detection is a heuristic that can be wrong, so
:func:`parse_logs` always falls back to the generic parser rather than failing
(SPEC §7.3, §8.5).
"""

from __future__ import annotations

from app.logengine.models import Runtime
from app.logengine.parsers.base import LogParser
from app.logengine.parsers.dotnet_logs import DotNetParser
from app.logengine.parsers.generic import GenericParser
from app.logengine.parsers.jsonl import JsonlParser
from app.logengine.parsers.nodejs_logs import NodeParser
from app.logengine.parsers.python_logs import PythonParser

_PARSERS: dict[Runtime, LogParser] = {
    Runtime.PYTHON: PythonParser(),
    Runtime.NODEJS: NodeParser(),
    Runtime.DOTNET: DotNetParser(),
    Runtime.JSONL: JsonlParser(),
    Runtime.GENERIC: GenericParser(),
}


def detect_runtime(text: str) -> Runtime:
    """Guess the log format from the content."""
    if JsonlParser().can_parse(text):
        return Runtime.JSONL
    if PythonParser().can_parse(text):
        return Runtime.PYTHON
    if NodeParser().can_parse(text):
        return Runtime.NODEJS
    if DotNetParser().can_parse(text):
        return Runtime.DOTNET
    return Runtime.GENERIC


def get_parser(runtime: Runtime) -> LogParser:
    """Return the parser for ``runtime``."""
    return _PARSERS.get(runtime, _PARSERS[Runtime.GENERIC])
