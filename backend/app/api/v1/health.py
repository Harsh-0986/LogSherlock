"""Liveness endpoint (SPEC §10.1). Checks the API process only."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Minimal liveness payload."""

    status: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return 200 when the API process is serving requests."""
    return HealthResponse(status="ok")
