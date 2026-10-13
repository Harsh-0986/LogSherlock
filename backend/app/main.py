"""LogSherlock FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.router import api_router
from app.config import get_settings


def _configure_logging(level: str) -> None:
    """Configure structured-enough logging without ever logging log content."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hooks."""
    settings = get_settings()
    _configure_logging(settings.log_level)
    logging.getLogger("logsherlock").info(
        "Starting %s (env=%s, provider=%s)",
        settings.app_name,
        settings.app_env,
        settings.model_provider,
    )
    yield
    logging.getLogger("logsherlock").info("Shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        summary="Evidence-backed AI log investigation",
        lifespan=lifespan,
    )

    # CORS restricted to configured origins only (SPEC §12.5).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
    )

    register_exception_handlers(app)

    app.include_router(api_router, prefix="/api/v1")
    # Convenience alias so `GET /health` also works at the root.
    app.include_router(api_router, prefix="", tags=["root-alias"])

    return app


app = create_app()
