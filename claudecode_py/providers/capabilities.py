from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class ProviderCapabilities:
    provider: str
    model: str
    supports_tool_calling: bool
    supports_streaming: bool
    supports_structured_output: bool
    supports_prompt_cache_hints: bool = False
    supports_system_prompt_cache_blocks: bool = False
    supports_tool_schema_cache_hints: bool = False
    notes: str = ""


def format_capabilities(capabilities: ProviderCapabilities) -> str:
    def yes_no(value: bool) -> str:
        return "yes" if value else "no"

    lines = [
        f"provider: {capabilities.provider}",
        f"model: {capabilities.model}",
        f"tool_calling: {yes_no(capabilities.supports_tool_calling)}",
        f"streaming: {yes_no(capabilities.supports_streaming)}",
        f"structured_output: {yes_no(capabilities.supports_structured_output)}",
        f"prompt_cache_hints: {yes_no(capabilities.supports_prompt_cache_hints)}",
        f"system_prompt_cache_blocks: {yes_no(capabilities.supports_system_prompt_cache_blocks)}",
        f"tool_schema_cache_hints: {yes_no(capabilities.supports_tool_schema_cache_hints)}",
    ]
    if capabilities.notes:
        lines.append(f"notes: {capabilities.notes}")
    return "\n".join(lines)
