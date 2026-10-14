"""Fake model provider for tests (SPEC §15.3).

Unit tests must never require a live model. This implements
:class:`~app.models.base.BaseChatModel` and returns scripted responses.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from pydantic import BaseModel

from app.models.base import (
    BaseChatModel,
    ChatMessage,
    ModelHealth,
    ModelMetadata,
    ModelResponse,
    ModelTimeoutError,
    ModelUnavailableError,
)


class FakeModel(BaseChatModel):
    """Scripted provider.

    Args:
        responses: Returned in order; the last one repeats once exhausted.
        error: If set, every call raises this exception.
    """

    provider_name = "fake"

    def __init__(
        self,
        responses: list[str] | None = None,
        *,
        error: Exception | None = None,
        model_name: str = "fake-model",
        responder: Callable[[list[ChatMessage]], str] | None = None,
    ) -> None:
        """Initialize with scripted responses and an optional failure."""
        self._responses = responses or []
        self._error = error
        self._model_name = model_name
        self._responder = responder
        self.calls: list[list[ChatMessage]] = []

    def metadata(self) -> ModelMetadata:
        """Return non-secret metadata."""
        return ModelMetadata(provider=self.provider_name, model_name=self._model_name)

    async def health(self) -> ModelHealth:
        """Report availability."""
        return ModelHealth(self.provider_name, self._model_name, self._error is None, "")

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: type[BaseModel] | None = None,
    ) -> ModelResponse:
        """Return the next scripted response."""
        self.calls.append(messages)

        if self._error is not None:
            raise self._error

        if self._responder is not None:
            return ModelResponse(
                text=self._responder(messages),
                provider=self.provider_name,
                model_name=self._model_name,
            )

        if not self._responses:
            raise ModelUnavailableError("FakeModel has no scripted response")

        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return ModelResponse(
            text=self._responses[index],
            provider=self.provider_name,
            model_name=self._model_name,
        )

    @property
    def last_prompt(self) -> str:
        """The user prompt from the most recent call."""
        return self.calls[-1][-1].content if self.calls else ""

    @property
    def system_prompt(self) -> str:
        """The system prompt from the most recent call."""
        return self.calls[0][0].content if self.calls else ""


def make_report(
    *,
    fingerprint: str,
    evidence_ids: list[str],
    title: str = "Database connection failures",
    confidence: str = "medium",
    severity: str = "high",
    summary: str = "The application repeatedly failed to connect to the database.",
) -> str:
    """Build a valid ModelReport JSON string for tests."""
    payload = {
        "summary": summary,
        "findings": [
            {
                "fingerprint": fingerprint,
                "title": title,
                "severity": severity,
                "likely_cause": "The database endpoint may be unavailable or unreachable.",
                "confidence": confidence,
                "evidence_ids": evidence_ids,
                "reasoning_summary": "Repeated connection errors across the selected events.",
                "alternative_hypotheses": [
                    "Incorrect connection configuration",
                    "Network or firewall interruption",
                ],
                "missing_information": ["Database health status"],
                "recommended_steps": [
                    {
                        "action": "Verify database reachability from the app environment.",
                        "reason": "Failures are consistent with connectivity problems.",
                        "verification": "Attempt a connection from the application's network.",
                    }
                ],
            }
        ],
        "limitations": [],
    }
    return json.dumps(payload)


def make_finding(
    *,
    title: str = "Database connection failed",
    evidence_ids: list[str] | None = None,
    confidence: str = "medium",
    severity: str = "high",
) -> str:
    """Build a single per-group finding JSON string (the current call shape)."""
    payload = {
        "title": title,
        "severity": severity,
        "likely_cause": "The database endpoint may be unavailable or unreachable.",
        "confidence": confidence,
        "evidence_ids": evidence_ids or ["line-1"],
        "reasoning_summary": "Repeated connection errors across the cited events.",
        "alternative_hypotheses": ["Incorrect connection configuration"],
        "missing_information": ["Database health status"],
        "recommended_steps": [
            {
                "action": "Verify database reachability from the app environment.",
                "reason": "Failures are consistent with connectivity problems.",
                "verification": "Attempt a connection from the application network.",
            }
        ],
    }
    return json.dumps(payload)


def echo_prompts(*, template: str = "finding") -> Callable[[list[ChatMessage]], str]:
    """Build a responder that answers each group prompt with its own title.

    The group title is read back out of the prompt so each call produces a
    finding that matches the group it was asked about, which is what the real
    per-group flow depends on.
    """
    import re

    def respond(messages: list[ChatMessage]) -> str:
        prompt = messages[-1].content
        title_match = re.search(r"^- title: (.+)$", prompt, re.M)
        title = title_match.group(1).strip() if title_match else "Database connection failed"
        ids = re.findall(r"^\[(line-\d+)\]", prompt, re.M)[:2] or ["line-1"]
        if template == "garbage":
            return "this is not json"
        return make_finding(title=title, evidence_ids=ids)

    return respond


def responder(fn: Callable[[list[ChatMessage]], str]) -> FakeModel:
    """Build a FakeModel whose response is computed from the prompt."""
    return FakeModel(responder=fn)


__all__ = ["FakeModel", "ModelTimeoutError", "ModelUnavailableError", "make_report", "responder"]
