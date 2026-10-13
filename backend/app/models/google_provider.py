"""Google AI Studio provider — Gemma family via the google-genai SDK.

Gemma weights are open-weight but this provider is *hosted*: redacted evidence is
sent to Google over the network. It is therefore opt-in and never the silent
fallback when Ollama is configured (SPEC §12.3).

Requires an API key in the environment.

Three things this adapter must get right, all learned from running it against a
real Gemma 4 build:

1. **Gemma 4 is a thinking model.** Its `thoughts_token_count` is drawn from the
   same output budget as the answer, so a small ``max_output_tokens`` is spent
   entirely on reasoning and the response arrives with no text at all. The
   thinking budget is therefore configurable and defaults to disabled — for
   structured extraction the answer is what we want, not the reasoning.
2. **This build rejects provider-side ``response_schema`` (HTTP 400).** We do not
   use it: the backend validates the response against Pydantic deterministically
   anyway (SPEC §9.2), so a provider-side schema adds a failure mode for nothing.
3. **HTTP 200 does not mean success.** The SDK can return 200 whose body carries
   an error. Error mapping is done on the parsed status, not the transport.
"""

from __future__ import annotations

import contextlib
import logging
import re
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

logger = logging.getLogger("logsherlock.model")

#: Provider errors that mean "the model answered, but we cannot use the answer".
_BAD_REQUEST_HINTS = ("400", "invalid argument", "invalid_argument", "unsupported")


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
        max_output_tokens: int = 8192,
        thinking_budget: int | None = None,
    ) -> None:
        """Initialize with a Google AI Studio API key.

        Args:
            api_key: Google AI Studio credential. Never logged or returned.
            model_name: Model id, e.g. ``gemma-3-27b-it``.
            timeout_seconds: Per-request timeout.
            temperature: Sampling temperature; low keeps output predictable.
            max_output_tokens: Combined allowance for reasoning *and* answer.
            thinking_budget: Tokens the model may spend reasoning. ``None``
                (the default) sends no thinking_config at all — several Gemma
                builds reject the parameter outright. Set an int only for a
                model that documents support for it.
        """
        from google import genai  # imported lazily so the SDK stays optional

        self._model_name = model_name
        self._timeout = timeout_seconds
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._thinking_budget = thinking_budget
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
        """Probe the configured model."""
        try:
            await self._client.aio.models.get(model=self._model_name)
        except Exception as exc:  # provider raises many SDK-specific types
            return ModelHealth(
                self.provider_name, self._model_name, False, f"Unavailable: {_short(exc)}"
            )
        return ModelHealth(self.provider_name, self._model_name, True, "Model available")

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: type[BaseModel] | None = None,
    ) -> ModelResponse:
        """Send a generation request and return the answer text.

        ``response_schema`` is accepted for interface compatibility but
        deliberately ignored: this provider rejects it, and the backend performs
        stricter validation itself (SPEC §9.2).
        """
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

        # Only send a thinking budget when one was explicitly configured; some
        # builds reject the parameter entirely.
        if self._thinking_budget is not None:
            with contextlib.suppress(Exception):  # older SDK without ThinkingConfig
                config_kwargs["thinking_config"] = genai_types.ThinkingConfig(
                    thinking_budget=self._thinking_budget
                )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model_name,
                contents=contents,
                config=genai_types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as exc:  # normalize SDK errors onto our typed errors
            raise _classify(exc) from exc

        return ModelResponse(
            text=self._extract_text(response),
            provider=self.provider_name,
            model_name=self._model_name,
            raw=_usage(response),
        )

    # -- internals --------------------------------------------------------

    def _extract_text(self, response: Any) -> str:
        """Pull the answer out of a response, diagnosing the empty cases.

        A thinking model that exhausts its budget returns parts that are all
        ``thought=True`` and no answer text. That is a budget problem, not an
        empty response, and the caller needs to be told which.
        """
        finish = _finish_reason(response)

        if finish and "MAX_TOKENS" in str(finish):
            usage = _usage(response)
            thoughts = usage.get("thoughts_token_count")
            raise ModelInvalidResponseError(
                "Model used its entire output budget on reasoning and returned no answer. "
                f"thoughts_token_count={thoughts}. "
                "Raise MODEL_MAX_OUTPUT_TOKENS so reasoning cannot starve the answer."
            )

        text = _answer_text(response)
        if not text.strip():
            raise ModelInvalidResponseError(
                f"Model returned no answer text (finish_reason={finish})."
            )
        return text


def _answer_text(response: Any) -> str:
    """Join the non-thought parts of the first candidate."""
    chunks: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        parts = getattr(getattr(candidate, "content", None), "parts", None) or []
        for part in parts:
            # Thinking models interleave thought parts; only answer parts count.
            if getattr(part, "thought", False):
                continue
            value = getattr(part, "text", None)
            if isinstance(value, str):
                chunks.append(value)

    if chunks:
        return "\n".join(chunks)

    text = getattr(response, "text", None)
    return text if isinstance(text, str) else ""


def _finish_reason(response: Any) -> Any:
    """Return the first candidate's finish reason, if any."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    return getattr(candidates[0], "finish_reason", None)


def _usage(response: Any) -> dict[str, Any]:
    """Summarise token usage, for diagnostics."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {}
    return {
        "prompt_tokens": getattr(usage, "prompt_token_count", None),
        "candidates_tokens": getattr(usage, "candidates_token_count", None),
        "thoughts_tokens": getattr(usage, "thoughts_token_count", None),
        "total_tokens": getattr(usage, "total_token_count", None),
    }


def _classify(exc: Exception) -> Exception:
    """Map a provider error onto the right typed error.

    A 400 from the provider is a request problem, not an outage, and reporting
    it as "unavailable" sends the reader looking in the wrong place.
    """
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()

    if "timeout" in lowered or "deadline" in lowered or "504" in lowered:
        return ModelTimeoutError("Google request timed out.")

    if any(hint in lowered for hint in _BAD_REQUEST_HINTS):
        return ModelInvalidResponseError(f"Google rejected the request: {_short(exc)}")

    if "500" in lowered or "internal" in lowered or "overloaded" in lowered or "503" in lowered:
        return ModelUnavailableError(f"Google returned a server error: {_short(exc)}")

    return ModelUnavailableError(f"Google request failed: {_short(exc)}")


def _short(exc: Exception, limit: int = 240) -> str:
    """Collapse whitespace and trim a provider message."""
    return re.sub(r"\s+", " ", str(exc)).strip()[:limit]
