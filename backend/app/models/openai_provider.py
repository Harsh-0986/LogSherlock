"""OpenAI provider — the official OpenAI API and any OpenAI-compatible endpoint.

``ChatOpenAI``      targets api.openai.com (requires an OpenAI key).
``ChatOpenAICompat`` targets a custom ``base_url`` (OpenRouter, vLLM, LM Studio,
                    llama.cpp server, or a local OpenAI-compatible gateway).

Both share this implementation; the difference is only the base URL and whether
a credential is required. They remain distinct classes so the operator's intent
is explicit in configuration (SPEC §5.2: providers must be explicitly configured).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from app.models.base import (
    BaseChatModel,
    ChatMessage,
    ModelHealth,
    ModelInvalidResponseError,
    ModelMetadata,
    ModelResponse,
    ModelTimeoutError,
    ModelUnavailableError,
)

# Used when a local OpenAI-compatible server does not require auth.
_PLACEHOLDER_KEY = "not-needed"


class _OpenAICompatible(BaseChatModel):
    """Shared implementation for both OpenAI-shaped providers."""

    provider_name = "openai"
    _default_base_url: str = "https://api.openai.com/v1"
    _requires_key: bool = True

    def __init__(
        self,
        *,
        model_name: str,
        timeout_seconds: int = 90,
        temperature: float = 0.1,
        max_output_tokens: int = 2048,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        from openai import AsyncOpenAI  # lazy import keeps the SDK optional

        self._base_url = (base_url or self._default_base_url).rstrip("/")
        self._model_name = model_name
        self._timeout = timeout_seconds
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens

        key = api_key or (_PLACEHOLDER_KEY if not self._requires_key else None)
        if self._requires_key and not key:
            raise ValueError(f"{type(self).__name__} requires an API key (set MODEL_API_KEY)")
        self._client = AsyncOpenAI(api_key=key, base_url=self._base_url, timeout=self._timeout)

    def metadata(self) -> ModelMetadata:
        """Return non-secret metadata. The API key is never included."""
        return ModelMetadata(
            provider=self.provider_name,
            model_name=self._model_name,
            base_url=self._base_url,
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
        )

    async def health(self) -> ModelHealth:
        """Confirm the endpoint answers and the model is servable."""
        try:
            await self._client.models.retrieve(self._model_name)
        except Exception as exc:
            return ModelHealth(
                self.provider_name, self._model_name, False, f"Unavailable: {type(exc).__name__}"
            )
        return ModelHealth(self.provider_name, self._model_name, True, "Model available")

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: type[BaseModel] | None = None,
    ) -> ModelResponse:
        """Send a chat completion, using structured output when a schema is given."""
        kwargs: dict[str, Any] = {
            "model": self._model_name,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self._temperature,
            "max_tokens": self._max_output_tokens,
        }
        if response_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.__name__,
                    "schema": response_schema.model_json_schema(),
                    "strict": True,
                },
            }

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            name = type(exc).__name__
            text = str(exc).lower()
            if "timeout" in name.lower() or "timeout" in text:
                raise ModelTimeoutError(f"OpenAI request timed out after {self._timeout}s") from exc
            raise ModelUnavailableError(f"OpenAI request failed: {name}") from exc

        if not response.choices:
            raise ModelInvalidResponseError("OpenAI returned no choices")

        text = response.choices[0].message.content
        if not isinstance(text, str) or not text.strip():
            raise ModelInvalidResponseError("OpenAI returned an empty message")
        return ModelResponse(
            text=text,
            provider=self.provider_name,
            model_name=self._model_name,
            raw={"id": getattr(response, "id", None), "model": getattr(response, "model", None)},
        )

    @staticmethod
    def parse_json(text: str) -> dict[str, Any]:
        """Parse a text response into JSON, tolerating a fenced code block."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ModelInvalidResponseError(f"Response was not valid JSON: {exc}") from exc


class ChatOpenAI(_OpenAICompatible):
    """The official OpenAI API."""

    provider_name = "openai"
    _default_base_url = "https://api.openai.com/v1"
    _requires_key = True


class ChatOpenAICompat(_OpenAICompatible):
    """Any OpenAI-compatible endpoint via a custom ``MODEL_BASE_URL``."""

    provider_name = "openai_compat"
    _default_base_url = "http://localhost:8000/v1"
    _requires_key = False
