from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class ProviderCapabilities:
    provider: str
    model: str
    supports_tool_calling: bool
    supports_streaming: bool
    supports_structured_output: bool
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
    ]
    if capabilities.notes:
        lines.append(f"notes: {capabilities.notes}")
    return "\n".join(lines)
