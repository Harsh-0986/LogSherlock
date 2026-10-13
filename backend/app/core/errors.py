"""Typed application errors.

Every error carries a machine-readable ``code`` and a message that is safe to
show a user. Raw log content and credentials must never appear in a message
(SPEC §12.3, §14).
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for errors that map onto an HTTP response."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
    ) -> None:
        """Initialize with a user-safe message and optional structured details."""
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code:
            self.code = code

    def to_payload(self) -> dict[str, Any]:
        """Render the SPEC §10.9 error envelope."""
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class InvalidInputError(AppError):
    """The caller supplied structurally invalid input."""

    code = "INVALID_INPUT"
    status_code = 400


class ValidationError(AppError):
    """Request body failed schema validation."""

    code = "VALIDATION_ERROR"
    status_code = 422


class NotFoundError(AppError):
    """The addressed resource does not exist."""

    code = "NOT_FOUND"
    status_code = 404


class PayloadTooLargeError(AppError):
    """Input exceeded a configured size limit (SPEC §7.2)."""

    code = "PAYLOAD_TOO_LARGE"
    status_code = 413


class UnsupportedFileTypeError(AppError):
    """Uploaded file extension or content type is not accepted."""

    code = "UNSUPPORTED_FILE_TYPE"
    status_code = 400


class RateLimitedError(AppError):
    """Too many investigations are already running (SPEC §10.9)."""

    code = "RATE_LIMITED"
    status_code = 429


class ServiceUnavailableError(AppError):
    """A required downstream service is unavailable."""

    code = "SERVICE_UNAVAILABLE"
    status_code = 503


class PersistenceError(AppError):
    """The database rejected or could not complete the operation."""

    code = "PERSISTENCE_ERROR"
    status_code = 500
