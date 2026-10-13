"""Ollama provider — local, privacy-first inference (SPEC §4.3, §18).

Runs an open-weight model on the developer's own machine via the Ollama daemon.
This is the default provider because it keeps proprietary logs local.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
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


class ChatOllama(BaseChatModel):
    """Chat model served by a local Ollama instance."""

    provider_name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model_name: str,
        timeout_seconds: int = 90,
        temperature: float = 0.1,
        max_output_tokens: int = 2048,
    ) -> None:
        """Initialize against a local Ollama endpoint."""
        self._base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._timeout = timeout_seconds
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens

    def metadata(self) -> ModelMetadata:
        """Return non-secret metadata."""
        return ModelMetadata(
            provider=self.provider_name,
            model_name=self._model_name,
            base_url=self._base_url,
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
        )

    async def health(self) -> ModelHealth:
        """Check the daemon and whether the model is pulled."""
        try:
            async with httpx.AsyncClient(timeout=min(self._timeout, 10)) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                resp.raise_for_status()
                payload = resp.json()
        except httpx.TimeoutException:
            return ModelHealth(self.provider_name, self._model_name, False, "Ollama timed out")
        except httpx.HTTPError as exc:
            return ModelHealth(
                self.provider_name, self._model_name, False, f"Ollama unreachable: {exc}"
            )

        models = {m.get("name", "") for m in payload.get("models", [])}
        base_model = self._model_name.split(":")[0]
        pulled = self._model_name in models or any(m.split(":")[0] == base_model for m in models)
        if pulled:
            return ModelHealth(self.provider_name, self._model_name, True, "Model available")
        return ModelHealth(
            self.provider_name, self._model_name, False, "Ollama reachable but model not pulled"
        )

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: type[BaseModel] | None = None,
    ) -> ModelResponse:
        """Call Ollama's /api/chat, optionally with a JSON schema."""
        fmt: Any = "json" if response_schema else None
        if response_schema:
            fmt = response_schema.model_json_schema()

        payload = {
            "model": self._model_name,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {
                "temperature": self._temperature,
                "num_predict": self._max_output_tokens,
            },
        }
        if fmt:
            payload["format"] = fmt

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(f"Ollama request timed out after {self._timeout}s") from exc
        except httpx.HTTPError as exc:
            raise ModelUnavailableError(f"Ollama request failed: {exc}") from exc

        text = (data.get("message") or {}).get("content")
        if not isinstance(text, str) or not text.strip():
            raise ModelInvalidResponseError("Ollama returned an empty message")
        return ModelResponse(
            text=text,
            provider=self.provider_name,
            model_name=self._model_name,
            raw=data if isinstance(data, dict) else {},
        )

    @staticmethod
    def parse_json(text: str) -> dict[str, Any]:
        """Best-effort parse of an Ollama text response into JSON."""
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ModelInvalidResponseError(f"Ollama response was not valid JSON: {exc}") from exc
