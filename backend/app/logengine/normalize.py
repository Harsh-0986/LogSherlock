"""Normalization of variable values (SPEC §7.5).

The goal is to stop high-cardinality values (timestamps, request IDs, UUIDs,
addresses) from fragmenting one recurring error into many groups, while
*preserving* diagnostically meaningful values: HTTP status codes, exception
types, database error codes, endpoint names, SQLSTATE codes.

The raw event is never modified — normalization produces a separate value.
"""

from __future__ import annotations

import re

# --- Variable patterns, replaced with stable tokens -----------------------

_VARIABLE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Timestamps (ISO and common printed forms). Order matters: most specific
    # first so we do not shred a date that a later rule wanted whole.
    (
        re.compile(
            r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?(?:Z|[+\-]\d{2}:?\d{2})?"
        ),
        "<TS>",
    ),
    (re.compile(r"\b\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}(?:[+\-]\d{4})?\b"), "<TS>"),
    # UUID v4-ish
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<UUID>",
    ),
    # Request / trace / correlation ids in labelled or prefixed form
    (
        re.compile(
            r"(?i)\b(request[_\-]?id|req[_\-]?id|trace[_\-]?id|correlation[_\-]?id|x-request-id|"
            r"span[_\-]?id|job[_\-]?id|task[_\-]?id|session[_\-]?id)\s*[:=]\s*[A-Za-z0-9._\-]{4,}"
        ),
        r"\1=<ID>",
    ),
    (
        re.compile(r"\b(?:req|reqid|trace|trc|span|sess|task|job)[-_]([0-9a-f]{8,})\b", re.I),
        r"<\1>",
    ),
    # Hex blobs that look like ids/hashes
    (re.compile(r"\b[0-9a-f]{16,}\b"), "<HASH>"),
    # IP addresses (keep port)
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<IP>"),
    # Durations / sizes — the value varies but the unit is the signal
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|milliseconds?|s|seconds?|us|µs)\b", re.I), "<DUR>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:bytes?|[KMGT]B|MiB|GiB|kB)\b", re.I), "<SIZE>"),
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "<EMAIL>"),
    # Long quoted/bracket tokens that are almost certainly runtime values
    (re.compile(r"\b0x[0-9a-fA-F]{6,}\b"), "<ADDR>"),
    # Bare numeric tokens (record ids, order numbers, ports, counts). Placed
    # LAST so durations, sizes and the stashed diagnostic codes above have
    # already been handled — this is what stops "order 88213" and "order 88214"
    # from becoming two separate groups (SPEC §7.5, numeric IDs).
    (re.compile(r"\b\d+\b"), "<N>"),
)

# --- Preserved patterns -------------------------------------------------
# Applied AFTER variable rules so a digit run inside one of these is restored.
# This is what stops normalization from destroying the diagnostic signal.

# HTTP status codes: "500 Internal Server Error", "status=503", "HTTP/1.1 404"
_HTTP_STATUS = re.compile(
    r"(?i)\b(?:status(?:_code)?\s*[:=]?\s*|HTTP/\d(?:\.\d)?\s+)"
    r"([1-5]\d{2})\b"
)
# PostgreSQL SQLSTATE: "SQLSTATE[08006]", "08006"
_SQLSTATE = re.compile(r"\bSQLSTATE\[?([0-9A-Z]{5})\]?\b")
# Named DB error codes, e.g. "Error 1205", "ORA-01722", "-2147467259"
_DB_ERROR_CODE = re.compile(
    r"\b(?:ORA-\d{5}|SQL Server Error \d+|(?:Error|error|code)\s*[:=]\s*-?\d{4,6})\b"
)
# .NET / HRESULT style codes
_HRESULT = re.compile(r"\b(?:0x[0-9A-F]{8}|HResult\s*[:=]\s*-?\d+|-2147\d{6})\b")

# --- Sentinel used to park preserved codes ------------------------------
# Must contain NO digits, or the bare-number rule below would rewrite the
# index itself. Private-use code points also cannot appear in real log text.
_SENTINEL_START = ""
_SENTINEL_END = ""


def _sentinel(index: int) -> str:
    """Encode ``index`` as letters inside a digit-free sentinel."""
    letters = ""
    remaining = index
    for _ in range(26):
        letters = chr(ord("A") + remaining % 26) + letters
        remaining //= 26
    return f"{_SENTINEL_START}{letters}{_SENTINEL_END}"


def normalize_message(message: str) -> str:
    """Return a normalized form of ``message`` for fingerprinting.

    Meaningful values (HTTP status, SQLSTATE, DB codes, HResults) are preserved
    verbatim; only genuinely variable values are replaced.
    """
    if not message:
        return ""

    out = message

    # Preserve diagnostically important codes before variable substitution.
    preserved: list[str] = []

    def _stash(pattern: re.Pattern[str]) -> None:
        nonlocal out

        def _sub(match: re.Match[str]) -> str:
            preserved.append(match.group(0))
            return _sentinel(len(preserved) - 1)

        out = pattern.sub(_sub, out)

    _stash(_SQLSTATE)
    _stash(_DB_ERROR_CODE)
    _stash(_HRESULT)
    _stash(_HTTP_STATUS)

    for pattern, replacement in _VARIABLE_RULES:
        out = pattern.sub(replacement, out)

    # Collapse whitespace so line wrapping does not split one error into two.
    out = re.sub(r"\s+", " ", out).strip()

    # Restore preserved codes.
    for index, original in enumerate(preserved):
        out = out.replace(_sentinel(index), original)

    return out


def normalize_title(message: str, exception_type: str | None, limit: int = 120) -> str:
    """Build a short, stable human title for an error group."""
    base = (message or "").strip()
    title = (f"{exception_type}: {base}" if base else exception_type) if exception_type else base
    title = re.sub(r"\s+", " ", title).strip()
    if len(title) > limit:
        title = title[: limit - 1].rstrip() + "…"
    return title or "Unknown error"


def normalize_for_display(text: str, *, max_length: int = 500) -> str:
    """Trim a message for display without destroying structure."""
    collapsed = re.sub(r"\s+", " ", text or "").strip()
    if len(collapsed) <= max_length:
        return collapsed
    return collapsed[: max_length - 1].rstrip() + "…"
