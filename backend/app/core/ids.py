"""Core primitives: identifiers and time helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

# Prefixes make log lines self-describing: "inv_3f2a..." vs a bare uuid.
_INVESTIGATION_PREFIX = "inv"
_FINDING_PREFIX = "fnd"
_GROUP_PREFIX = "grp"


def new_investigation_id() -> str:
    """Return a new opaque investigation identifier."""
    return f"{_INVESTIGATION_PREFIX}_{uuid.uuid4().hex[:16]}"


def new_finding_id() -> str:
    """Return a new finding identifier."""
    return f"{_FINDING_PREFIX}_{uuid.uuid4().hex[:12]}"


def new_group_id() -> str:
    """Return a new error-group identifier."""
    return f"{_GROUP_PREFIX}_{uuid.uuid4().hex[:12]}"


def utc_now() -> datetime:
    """Timezone-aware current time. Always UTC, never naive."""
    return datetime.now(UTC)


def to_iso(value: datetime | None) -> str | None:
    """Serialise a datetime to an ISO-8601 string, or None."""
    return value.isoformat() if value is not None else None
