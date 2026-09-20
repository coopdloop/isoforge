"""LLM provider registry."""

from __future__ import annotations

import os

from .anthropic import AnthropicProvider
from .base import (
    CompletionResult,
    LLMProvider,
    Message,
    ProviderError,
    ToolCall,
    ToolSpec,
)
from .ollama import OllamaProvider
from .openrouter import OpenRouterProvider

PROVIDERS: dict[str, type[LLMProvider]] = {
    "openrouter": OpenRouterProvider,
    "anthropic": AnthropicProvider,
    "ollama": OllamaProvider,
}

DEFAULT_PROVIDER = "openrouter"


def build_provider(
    name: str | None = None, model: str | None = None, **kwargs
) -> LLMProvider:
    """Instantiate a provider by name, honouring DEFAULT_LLM_PROVIDER."""
    name = (name or os.environ.get("DEFAULT_LLM_PROVIDER") or DEFAULT_PROVIDER).lower()
    cls = PROVIDERS.get(name)
    if cls is None:
        raise ValueError(
            f"unknown provider '{name}'; available: {', '.join(sorted(PROVIDERS))}"
        )
    return cls(model, **kwargs) if model else cls(**kwargs)


def detect_available() -> list[str]:
    """Providers that look usable right now, in preference order.

    Used by `isoforge doctor` and by the CLI to pick a sane default without making the
    user read documentation first.
    """
    found: list[str] = []
    for name in ("openrouter", "anthropic", "ollama"):
        try:
            if PROVIDERS[name]().available():
                found.append(name)
        except Exception:  # noqa: BLE001 - detection must never raise
            continue
    return found


__all__ = [
    "PROVIDERS",
    "DEFAULT_PROVIDER",
    "AnthropicProvider",
    "CompletionResult",
    "LLMProvider",
    "Message",
    "OllamaProvider",
    "OpenRouterProvider",
    "ProviderError",
    "ToolCall",
    "ToolSpec",
    "build_provider",
    "detect_available",
]
