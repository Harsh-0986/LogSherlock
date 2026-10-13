"""Model provider abstraction. See :mod:`app.models.base` for the contract."""

from app.models.base import (
    BaseChatModel,
    ChatMessage,
    ModelError,
    ModelHealth,
    ModelInvalidResponseError,
    ModelMetadata,
    ModelResponse,
    ModelTimeoutError,
    ModelUnavailableError,
)
from app.models.registry import SUPPORTED_PROVIDERS, build_model

__all__ = [
    "SUPPORTED_PROVIDERS",
    "BaseChatModel",
    "ChatMessage",
    "ModelError",
    "ModelHealth",
    "ModelInvalidResponseError",
    "ModelMetadata",
    "ModelResponse",
    "ModelTimeoutError",
    "ModelUnavailableError",
    "build_model",
]
