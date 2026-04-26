from __future__ import annotations

from .anthropic import AnthropicProvider
from .capabilities import ProviderCapabilities, format_capabilities
from .openai_compatible import OpenAICompatibleProvider

__all__ = [
    "AnthropicProvider",
    "OpenAICompatibleProvider",
    "ProviderCapabilities",
    "build_provider",
    "format_capabilities",
]


def build_provider(*, provider: str, model: str, max_tokens: int, api_key: str | None, base_url: str | None):
    if provider == "anthropic":
        return AnthropicProvider(model=model, max_tokens=max_tokens, api_key=api_key)
    if provider == "openai-compatible":
        return OpenAICompatibleProvider(
            model=model,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url=base_url,
        )
    raise ValueError(f"Unsupported provider: {provider}")
