"""Model status endpoint (SPEC §10.2).

Reports the configured provider, model, and reachability. Never exposes API keys
or internal credentials.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.models.base import ModelHealth
from app.models.registry import build_model

logger = logging.getLogger("logsherlock.model")
router = APIRouter(prefix="/model", tags=["model"])


class ModelStatusResponse(BaseModel):
    """Configured provider, model, and whether it is reachable."""

    provider: str
    model: str
    available: bool
    detail: str = ""


def _unavailable(provider: str, model_name: str, detail: str) -> ModelStatusResponse:
    return ModelStatusResponse(provider=provider, model=model_name, available=False, detail=detail)


@router.get("/status", response_model=ModelStatusResponse)
async def model_status(settings: Settings = Depends(get_settings)) -> ModelStatusResponse:
    """Return the configured model and probe its reachability.

    If the model cannot be reached the endpoint still returns 200 with
    ``available: false`` — deterministic analysis remains usable.
    """
    try:
        model = build_model(settings)
    except ValueError as exc:
        # Misconfiguration (e.g. missing key) is reported, not raised.
        return _unavailable(settings.model_provider, settings.model_name, str(exc))

    try:
        health: ModelHealth = await model.health()
    except Exception as exc:
        logger.warning("Model health check raised: %s", type(exc).__name__)
        return _unavailable(settings.model_provider, settings.model_name, "Health check failed")

    return ModelStatusResponse(
        provider=health.provider,
        model=health.model_name,
        available=health.available,
        detail=health.detail,
    )
