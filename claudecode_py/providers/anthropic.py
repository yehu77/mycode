from __future__ import annotations

from collections.abc import Iterator
from typing import Any
import os

from ..models import AssistantResponse, ProviderStreamEvent, ToolCall
from .capabilities import ProviderCapabilities
from .errors import (
    ProviderCapabilityError,
    ProviderConfigurationError,
    ProviderNetworkError,
    ProviderTimeoutError,
    classify_status_error,
)


class AnthropicProvider:
    def __init__(self, *, model: str, max_tokens: int, api_key: str | None = None) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.api_key = api_key
        self._client = None
        self.capabilities = ProviderCapabilities(
            provider="anthropic",
            model=model,
            supports_tool_calling=True,
            supports_streaming=True,
            supports_structured_output=False,
            notes="Anthropic tool use and SDK streaming are enabled.",
        )

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise ProviderConfigurationError(
                'Missing dependency "anthropic". Install with: pip install -e .[anthropic]'
            ) from exc
        api_key = self.api_key or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ProviderConfigurationError("ANTHROPIC_API_KEY is not set.")
        self._client = Anthropic(api_key=api_key)
        return self._client

    def create_message(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system_prompt: str,
    ) -> AssistantResponse:
        client = self._ensure_client()
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_prompt,
                messages=messages,
                tools=tools,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap_error(exc) from exc

        return self._build_response_from_message(response)

    def stream_message(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system_prompt: str,
    ) -> Iterator[ProviderStreamEvent]:
        client = self._ensure_client()
        try:
            with client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_prompt,
                messages=messages,
                tools=tools,
            ) as stream:
                for text in stream.text_stream:
                    if text:
                        yield ProviderStreamEvent(kind="text_delta", text=text)
                final_message = stream.get_final_message()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap_error(exc) from exc

        yield ProviderStreamEvent(
            kind="response",
            response=self._build_response_from_message(final_message),
        )

    def _wrap_error(self, exc: Exception):
        status_code = getattr(exc, "status_code", None)
        message = str(exc)
        if status_code is not None:
            return classify_status_error(status_code, message)

        name = type(exc).__name__.lower()
        if "timeout" in name:
            return ProviderTimeoutError(f"Provider request timed out: {message}")
        if "connection" in name or "network" in name or "apiconnection" in name:
            return ProviderNetworkError(f"Provider network error: {message}")
        if "tool" in message.lower() and "support" in message.lower():
            return ProviderCapabilityError(f"Provider capability mismatch: {message}")
        return ProviderNetworkError(f"Anthropic provider call failed: {message}")

    def _build_response_from_message(self, response: Any) -> AssistantResponse:
        content: list[dict[str, Any]] = []
        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []

        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text = getattr(block, "text", "")
                content.append({"type": "text", "text": text})
                if text:
                    text_parts.append(text)
            elif block_type == "tool_use":
                block_id = getattr(block, "id", "")
                name = getattr(block, "name", "")
                tool_input = dict(getattr(block, "input", {}) or {})
                content.append(
                    {
                        "type": "tool_use",
                        "id": block_id,
                        "name": name,
                        "input": tool_input,
                    }
                )
                tool_calls.append(ToolCall(id=block_id, name=name, input=tool_input))
            else:
                raw = block.model_dump() if hasattr(block, "model_dump") else {"type": block_type}
                content.append(raw)

        return AssistantResponse(
            content=content,
            text="".join(text_parts).strip(),
            tool_calls=tool_calls,
            stop_reason=getattr(response, "stop_reason", None),
        )
