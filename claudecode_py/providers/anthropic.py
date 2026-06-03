from __future__ import annotations

from collections.abc import Iterator
from typing import Any
import os

from ..models import AssistantResponse, ProviderStreamEvent, TokenUsage, ToolCall
from .capabilities import ProviderCapabilities
from .errors import (
    ProviderCapabilityError,
    ProviderConfigurationError,
    ProviderContextLimitError,
    ProviderNetworkError,
    ProviderTimeoutError,
    classify_context_limit_error,
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
        context_limit_error = classify_context_limit_error(status_code, message)
        if context_limit_error is not None:
            return context_limit_error
        if status_code is not None:
            return classify_status_error(status_code, message)

        name = type(exc).__name__.lower()
        if "timeout" in name:
            return ProviderTimeoutError(f"Provider request timed out: {message}")
        if "connection" in name or "network" in name or "apiconnection" in name:
            return ProviderNetworkError(f"Provider network error: {message}")
        if "tool" in message.lower() and "support" in message.lower():
            return ProviderCapabilityError(f"Provider capability mismatch: {message}")
        if classify_context_limit_error(None, message) is not None:
            return ProviderContextLimitError(f"Anthropic provider call failed: {message}")
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
            usage=self._extract_usage(response),
        )

    def _extract_usage(self, response: Any) -> TokenUsage | None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        prompt_tokens = getattr(usage, "input_tokens", None)
        completion_tokens = getattr(usage, "output_tokens", None)
        total_tokens = None
        if prompt_tokens is not None or completion_tokens is not None:
            total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)
        if prompt_tokens is None and completion_tokens is None and total_tokens is None:
            return None
        return TokenUsage(
            prompt_tokens=int(prompt_tokens) if prompt_tokens is not None else None,
            completion_tokens=int(completion_tokens) if completion_tokens is not None else None,
            total_tokens=total_tokens,
        )
