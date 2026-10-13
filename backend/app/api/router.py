"""API package."""

from fastapi import APIRouter

from app.api.v1 import health, model

# All versioned routes are mounted under /api/v1 by the app factory.
api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(model.router)

__all__ = ["api_router"]
