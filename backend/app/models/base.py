"""Model provider abstraction (SPEC §9.4).

The rest of the application depends only on :class:`BaseChatModel` and the
typed :class:`ModelResponse` it returns. Concrete providers subclass it and own
their SDK, configuration, timeout handling, and response normalization.

Implementations in this package:
    * :class:`~app.models.ollama_provider.ChatOllama`     — local Ollama daemon
    * :class:`~app.models.google_provider.ChatGoogle`     — Google AI Studio (Gemma)
    * :class:`~app.models.openai_provider.ChatOpenAI`     — OpenAI / OpenAI-compatible

No provider falls back to another. If the configured provider is unreachable
the backend degrades to deterministic analysis (SPEC §8.5).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Role = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    """A single chat message."""

    model_config = ConfigDict(frozen=True)

    role: Role
    content: str


class ModelMetadata(BaseModel):
    """Non-secret description of the configured model."""

    model_config = ConfigDict(frozen=True)

    provider: str
    model_name: str
    base_url: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None


@dataclass(slots=True)
class ModelHealth:
    """Reachability result for the configured model."""

    provider: str
    model_name: str
    available: bool
    detail: str = ""


@dataclass(slots=True)
class ModelResponse:
    """Normalized response from any provider."""

    text: str
    provider: str
    model_name: str
    raw: dict[str, Any] = field(default_factory=dict)


class ModelError(Exception):
    """Base class for typed model failures (SPEC §9.4)."""


class ModelUnavailableError(ModelError):
    """The provider could not be reached."""


class ModelTimeoutError(ModelError):
    """The provider did not respond within the configured timeout."""


class ModelInvalidResponseError(ModelError):
    """The provider returned something that is not usable output."""


class BaseChatModel(abc.ABC):
    """Contract every model provider must satisfy."""

    #: Stable identifier used by config and registry.
    provider_name: str = "base"

    @abc.abstractmethod
    def metadata(self) -> ModelMetadata:
        """Return non-secret metadata about the configured model."""

    @abc.abstractmethod
    async def health(self) -> ModelHealth:
        """Check whether the provider is currently reachable."""

    @abc.abstractmethod
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: type[BaseModel] | None = None,
    ) -> ModelResponse:
        """Send messages and return a normalized response.

        Args:
            messages: Ordered conversation, system-first.
            response_schema: Optional Pydantic schema the provider should honor.

        Raises:
            ModelUnavailableError: Provider unreachable.
            ModelTimeoutError: Request exceeded the timeout.
            ModelInvalidResponseError: Response was not usable output.
        """

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        """Never leak the API key through repr."""
        return f"<{type(self).__name__} provider={self.provider_name}>"
