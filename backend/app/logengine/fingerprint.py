"""Deterministic error fingerprinting (SPEC §7.6).

A fingerprint is a stable hash of an error's *stable* characteristics:
exception type, normalized message, and the most relevant stack frames.

Two requirements pull against each other here:
1. The same underlying error, recurring with different ids/timestamps, must
   produce one fingerprint.
2. Two genuinely different errors must not collapse into one group just because
   they share a word like "failed".

The fingerprint therefore includes the exception type and the normalized message
wholesale, plus only the top stack frames — never a bag of keywords.
"""

from __future__ import annotations

import hashlib
import re

# File/line noise inside frames that should not split a group.
_FRAME_FILE = re.compile(r"\(([^)]*?)\)?$")
_NUMERIC = re.compile(r"\d+")

#: How many stack frames participate in the fingerprint.
FRAME_DEPTH = 3

#: Paths that indicate a project file (as opposed to node_modules/stdlib).
_NOISE_DIR = re.compile(r"(node_modules|[\\/]site-packages[\\/]|[\\/]\.venv[\\/]|python3\.\d+)")


def _frame_key(frame: str) -> str:
    """Reduce a stack frame to a stable, location-independent key."""
    cleaned = frame.strip()
    if not cleaned:
        return ""

    # JS: "at foo (/srv/app/src/db.js:12:5)" / "at Object.<anonymous> (/srv/app/src/db.js:1:1)"
    js = re.match(r"at\s+(?P<fn>.+?)\s+\((?P<loc>[^)]*)\)\s*$", cleaned)
    if js is not None:
        fn = js.group("fn").strip()
        loc = js.group("loc")
        # Strip line:col, keep the file's basename-ish tail.
        loc = re.sub(r":\d+:\d+$", "", loc)
        loc = _NOISE_DIR.sub("", loc)
        return f"js:{fn}@{loc.strip('/')}"

    # Python: 'File "/srv/app/db.py", line 42, in connect'
    py = re.search(r'File "(?P<file>[^"]+)", line \d+, in (?P<fn>[\w<>.]+)', cleaned)
    if py is not None:
        path = _NOISE_DIR.sub("", py.group("file"))
        return f"py:{py.group('fn')}@{path}"

    # .NET: "at App.Data.Repo.Connect() in /srv/App/Data/Repo.cs:line 42"
    dotnet = re.match(
        r"at\s+(?P<member>[\w.`]+)\s*\([^)]*\)"
        r"(?:\s+in\s+(?P<file>.+?):(?:line\s+)?\d+)?\s*$",
        cleaned,
    )
    if dotnet is not None:
        member = dotnet.group("member")
        file_part = dotnet.group("file") or ""
        file_part = re.sub(r":(?:line\s+)?\d+$", "", file_part)
        file_part = _NOISE_DIR.sub("", file_part)
        return f"cs:{member}@{file_part.strip('/')}"

    # Generic fallback: drop trailing numbers (line numbers), keep the rest.
    return "raw:" + _NUMERIC.sub("#", cleaned)[:120]


def extract_frames(stack_trace: str | None, depth: int = FRAME_DEPTH) -> tuple[str, ...]:
    """Return up to ``depth`` stable frame keys from a stack trace."""
    if not stack_trace:
        return ()
    frames: list[str] = []
    for raw_line in stack_trace.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("Traceback", "~~~", "Exception", "--->", "--- End")):
            continue
        if not (
            line.startswith("at ")
            or line.startswith('File "')
            or re.match(r"at\s+[\w.`]+\s*\(", line)
            or re.match(r"[A-Za-z_][\w.]*\.[A-Za-z_]\w*(Error|Exception)", line)
        ):
            continue
        key = _frame_key(line)
        if key:
            frames.append(key)
        if len(frames) >= depth:
            break
    return tuple(frames)


def compute_fingerprint(
    *,
    exception_type: str | None,
    normalized_message: str,
    frames: tuple[str, ...] = (),
    service: str | None = None,
) -> str:
    """Return a stable 16-hex-character fingerprint.

    Deterministic across processes and runs: no randomness, no timestamps, and
    no dict-order dependence.
    """
    parts: list[str] = [
        (exception_type or "-").strip().lower(),
        normalized_message.strip(),
    ]
    if frames:
        parts.append("|".join(frames))
    if service:
        parts.append(service.strip().lower())

    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8", "replace")).hexdigest()
    return digest[:16]
