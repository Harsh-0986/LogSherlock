"""Secret redaction (SPEC §12.2).

Runs before evidence is persisted or submitted to a model.

This is **best-effort pattern matching and cannot guarantee detection of every
secret** (SPEC §12.2). It is a safety net, not a guarantee. Documented in the
README and surfaced in the report's limitations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PLACEHOLDER_TOKEN = "[REDACTED_TOKEN]"
PLACEHOLDER_SECRET = "[REDACTED_SECRET]"
PLACEHOLDER_PASSWORD = "[REDACTED_PASSWORD]"
PLACEHOLDER_EMAIL = "[REDACTED_EMAIL]"
PLACEHOLDER_IP = "[REDACTED_IP]"
PLACEHOLDER_CONNSTR = "[REDACTED_CONNSTR]"

# Ordered: more specific patterns run first so a broader rule cannot claim a
# span that a narrower rule should have handled.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # postgres://user:password@host/db
    (
        PLACEHOLDER_CONNSTR,
        re.compile(
            r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://"
            r"[^\s:/@]+:[^\s/@]+@[^\s]+",
            re.IGNORECASE,
        ),
    ),
    # AWS access key id (AKIA / ASIA + 16 uppercase alphanumerics)
    (PLACEHOLDER_SECRET, re.compile(r"\b(?:AKIA|ASIA|AIDA|AROA)[0-9A-Z]{16}\b")),
    # Google API key
    (PLACEHOLDER_SECRET, re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    # Slack / GitHub style tokens
    (PLACEHOLDER_SECRET, re.compile(r"\bgh[pousr]_[0-9A-Za-z]{16,}\b")),
    (PLACEHOLDER_SECRET, re.compile(r"\bxox[abprs]-[0-9A-Za-z\-]{10,}\b")),
    # Generic JWT
    (
        PLACEHOLDER_TOKEN,
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
    ),
    # Authorization header / bearer token. The value is consumed to the end of
    # the field so the token itself is removed, not just the "Bearer" word.
    (
        PLACEHOLDER_TOKEN,
        re.compile(r"(?i)\b(authorization|proxy-authorization)\s*[:=]\s*[^\r\n;,]+"),
    ),
    (PLACEHOLDER_TOKEN, re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/]{12,}=*")),
    # key/secret/token/password assignment: api_key=..., "password": "...", etc.
    (
        PLACEHOLDER_SECRET,
        re.compile(
            r"(?i)\b([a-z0-9_\-]*(?:api[_\-]?key|secret|token|password|passwd|pwd|access[_\-]?key)"
            r"[a-z0-9_\-]*)\s*[:=]\s*[\"']?([^\s\"',;]{3,})[\"']?"
        ),
    ),
    # Session cookie
    (
        PLACEHOLDER_TOKEN,
        re.compile(r"(?i)\b(set-)?cookie\s*[:=]\s*[^\s;]{8,}"),
    ),
    # Email address
    (
        PLACEHOLDER_EMAIL,
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    ),
)

_IPV4 = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\b"
)


@dataclass(slots=True)
class RedactionResult:
    """Outcome of a redaction pass."""

    text: str
    #: Placeholder label -> number of replacements made.
    counts: dict[str, int] = field(default_factory=dict)
    changed: bool = False

    @property
    def total_redactions(self) -> int:
        """Total number of substitutions applied."""
        return sum(self.counts.values())


class Redactor:
    """Configurable, best-effort secret redactor."""

    def __init__(self, *, enabled: bool = True, redact_ip_addresses: bool = True) -> None:
        """Initialize with redaction toggles."""
        self._enabled = enabled
        self._redact_ips = redact_ip_addresses

    def redact(self, text: str) -> RedactionResult:
        """Return ``text`` with recognised secrets replaced by placeholders."""
        if not self._enabled or not text:
            return RedactionResult(text=text, changed=False)

        counts: dict[str, int] = {}
        out = text

        for placeholder, pattern in _RULES:
            out, n = pattern.subn(placeholder, out)
            if n:
                counts[placeholder] = counts.get(placeholder, 0) + n

        if self._redact_ips:
            out, n = _IPV4.subn(PLACEHOLDER_IP, out)
            if n:
                counts[PLACEHOLDER_IP] = counts.get(PLACEHOLDER_IP, 0) + n

        return RedactionResult(
            text=out,
            counts=counts,
            changed=out != text,
        )

    def redact_many(self, texts: list[str]) -> list[RedactionResult]:
        """Redact a batch of strings."""
        return [self.redact(t) for t in texts]
