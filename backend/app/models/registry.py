"""Model provider registry (SPEC §9.4).

Maps the configured ``MODEL_PROVIDER`` name to a concrete :class:`BaseChatModel`
implementation. The rest of the app calls :func:`build_model` and depends only on
the returned interface, never on provider-specific code.
"""

from __future__ import annotations

from collections.abc import Callable

from app.config import Settings
from app.models.base import BaseChatModel

ProviderFactory = Callable[[Settings], BaseChatModel]


def _build_ollama(settings: Settings) -> BaseChatModel:
    from app.models.ollama_provider import ChatOllama

    return ChatOllama(
        base_url=settings.model_base_url or "http://localhost:11434",
        model_name=settings.model_name,
        timeout_seconds=settings.model_timeout_seconds,
        temperature=settings.model_temperature,
        max_output_tokens=settings.model_max_output_tokens,
    )


def _build_google(settings: Settings) -> BaseChatModel:
    from app.models.google_provider import ChatGoogle

    key = settings.model_api_key
    if not key:
        raise ValueError("MODEL_PROVIDER=google requires MODEL_API_KEY (or GOOGLE_API_KEY)")
    return ChatGoogle(
        api_key=key,
        model_name=settings.model_name,
        timeout_seconds=settings.model_timeout_seconds,
        temperature=settings.model_temperature,
        max_output_tokens=settings.model_max_output_tokens,
    )


def _build_openai(settings: Settings) -> BaseChatModel:
    from app.models.openai_provider import ChatOpenAI

    return ChatOpenAI(
        model_name=settings.model_name,
        timeout_seconds=settings.model_timeout_seconds,
        temperature=settings.model_temperature,
        max_output_tokens=settings.model_max_output_tokens,
        base_url=settings.model_base_url,
        api_key=settings.model_api_key,
    )


def _build_openai_compat(settings: Settings) -> BaseChatModel:
    from app.models.openai_provider import ChatOpenAICompat

    return ChatOpenAICompat(
        model_name=settings.model_name,
        timeout_seconds=settings.model_timeout_seconds,
        temperature=settings.model_temperature,
        max_output_tokens=settings.model_max_output_tokens,
        base_url=settings.model_base_url,
        api_key=settings.model_api_key,
    )


_PROVIDERS: dict[str, ProviderFactory] = {
    "ollama": _build_ollama,
    "google": _build_google,
    "openai": _build_openai,
    "openai_compat": _build_openai_compat,
}

SUPPORTED_PROVIDERS: tuple[str, ...] = tuple(_PROVIDERS)


def build_model(settings: Settings) -> BaseChatModel:
    """Instantiate the configured provider.

    Raises:
        ValueError: If the provider name is unknown or required config is missing.
    """
    factory = _PROVIDERS.get(settings.model_provider)
    if factory is None:
        raise ValueError(
            f"Unknown MODEL_PROVIDER={settings.model_provider!r}. "
            f"Supported: {', '.join(SUPPORTED_PROVIDERS)}"
        )
    return factory(settings)
