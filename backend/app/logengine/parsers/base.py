"""Parser adapter interface (SPEC §7.3).

A parser turns raw text into :class:`~app.logengine.models.LogEvent` objects.
Parsers never fail the whole investigation: anything they cannot recognise is
handed to the generic parser (SPEC §7.3, §8.5).
"""

from __future__ import annotations

import abc

from app.logengine.models import LogEvent


class LogParser(abc.ABC):
    """Contract for every parser adapter."""

    #: Runtime this parser claims to handle.
    runtime: str = "generic"

    @abc.abstractmethod
    def parse(self, text: str) -> list[LogEvent]:
        """Parse ``text`` into events.

        Implementations must:
        - preserve 1-based line numbers,
        - attach continuation lines (tracebacks, stacks) to the event they belong to,
        - leave unextractable fields as ``None`` rather than guessing.
        """

    def can_parse(self, text: str) -> bool:
        """Return True when this parser is a good fit for ``text``."""
        return True
