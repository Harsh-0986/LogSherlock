"""Google AI Studio provider — Gemma family via the google-genai SDK.

Gemma weights are open-weight but this provider is *hosted*: log evidence is
sent to Google over the network. It is therefore opt-in and never the silent
fallback when Ollama is configured (SPEC §12.3).

Requires `GOOGLE_API_KEY` in the environment.
"""

from __future__ import annotations

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

_SYSTEM_KEY = "system"


class ChatGoogle(BaseChatModel):
    """Chat model served by Google AI Studio (Gemma)."""

    provider_name = "google"

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        timeout_seconds: int = 90,
        temperature: float = 0.1,
        max_output_tokens: int = 2048,
    ) -> None:
        """Initialize with a Google AI Studio API key."""
        from google import genai  # imported lazily so the SDK stays optional

        self._model_name = model_name
        self._timeout = timeout_seconds
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._client = genai.Client(api_key=api_key)

    def metadata(self) -> ModelMetadata:
        """Return non-secret metadata. The API key is never included."""
        return ModelMetadata(
            provider=self.provider_name,
            model_name=self._model_name,
            base_url=None,  # Google manages routing; no user-visible base URL
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
        )

    async def health(self) -> ModelHealth:
        """Probe with a minimal request to confirm the key and model work."""
        try:
            await self._client.aio.models.get(model=self._model_name)
        except Exception as exc:
            return ModelHealth(self.provider_name, self._model_name, False, f"Unavailable: {exc}")
        return ModelHealth(self.provider_name, self._model_name, True, "Model available")

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: type[BaseModel] | None = None,
    ) -> ModelResponse:
        """Send a generation request through the google-genai SDK."""
        from google.genai import types as genai_types

        system_parts = [m.content for m in messages if m.role == _SYSTEM_KEY]
        contents = [
            genai_types.Content(
                role="model" if m.role == "assistant" else "user",
                parts=[genai_types.Part(text=m.content)],
            )
            for m in messages
            if m.role != _SYSTEM_KEY
        ]

        config_kwargs: dict[str, Any] = {
            "temperature": self._temperature,
            "max_output_tokens": self._max_output_tokens,
        }
        if system_parts:
            config_kwargs["system_instruction"] = "\n\n".join(system_parts)
        if response_schema:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = response_schema

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model_name,
                contents=contents,
                config=genai_types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as exc:
            name = type(exc).__name__
            if "timeout" in name.lower() or "deadline" in str(exc).lower():
                raise ModelTimeoutError(f"Google request timed out: {exc}") from exc
            raise ModelUnavailableError(f"Google request failed: {exc}") from exc

        parsed = getattr(response, "parsed", None)
        if response_schema is not None and isinstance(parsed, response_schema):
            return ModelResponse(
                text=parsed.model_dump_json(),
                provider=self.provider_name,
                model_name=self._model_name,
            )

        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            raise ModelInvalidResponseError("Google returned an empty response")
        return ModelResponse(
            text=text,
            provider=self.provider_name,
            model_name=self._model_name,
        )
