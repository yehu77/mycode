from __future__ import annotations

from collections.abc import Iterator
from typing import Any
import json
import os

from ..models import AssistantResponse, ProviderStreamEvent, TokenUsage, ToolCall
from ..runtime.provider_cache import ProviderPromptCachePlan
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


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        model: str,
        max_tokens: int,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.api_key = api_key
        self.base_url = base_url
        self._client = None
        self.capabilities = ProviderCapabilities(
            provider="openai-compatible",
            model=model,
            supports_tool_calling=True,
            supports_streaming=True,
            supports_structured_output=False,
            supports_prompt_cache_hints=False,
            supports_system_prompt_cache_blocks=False,
            supports_tool_schema_cache_hints=False,
            notes=(
                "Assumes the selected model supports chat-completions tool calling. "
                "Streaming is wired into this runtime when the upstream API returns "
                "structured stream chunks."
            ),
        )

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderConfigurationError(
                'Missing dependency "openai". Install with: pip install -e .[openai]'
            ) from exc

        api_key = self.api_key or os.getenv("PYCLAUDE_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ProviderConfigurationError("OpenAI-compatible provider requires an API key.")

        self._client = OpenAI(api_key=api_key, base_url=self.base_url)
        return self._client

    def create_message(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system_prompt: str,
        cache_plan: ProviderPromptCachePlan | None = None,
        model_override: str | None = None,
        effort_override: str | None = None,
    ) -> AssistantResponse:
        del cache_plan, effort_override
        client = self._ensure_client()
        try:
            response = client.chat.completions.create(
                **self._request_kwargs(
                    messages=messages,
                    tools=tools,
                    system_prompt=system_prompt,
                    model_override=model_override,
                    stream=False,
                )
            )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap_error(exc) from exc
        choice = response.choices[0]
        return self._parse_message_response(
            choice.message,
            choice.finish_reason,
            usage=self._extract_usage(response),
        )

    def stream_message(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system_prompt: str,
        cache_plan: ProviderPromptCachePlan | None = None,
        model_override: str | None = None,
        effort_override: str | None = None,
    ) -> Iterator[ProviderStreamEvent]:
        del cache_plan, effort_override
        client = self._ensure_client()
        try:
            stream = client.chat.completions.create(
                **self._request_kwargs(
                    messages=messages,
                    tools=tools,
                    system_prompt=system_prompt,
                    model_override=model_override,
                    stream=True,
                )
            )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap_error(exc) from exc

        text_parts: list[str] = []
        tool_buffers: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        stream_usage: TokenUsage | None = None

        try:
            for chunk in stream:
                chunk_usage = self._extract_usage(chunk)
                if chunk_usage is not None:
                    stream_usage = chunk_usage
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue

                text_delta = getattr(delta, "content", None) or ""
                if text_delta:
                    text_parts.append(text_delta)
                    yield ProviderStreamEvent(kind="text_delta", text=text_delta)

                for tool_call in getattr(delta, "tool_calls", None) or []:
                    index = getattr(tool_call, "index", 0) or 0
                    buffer = tool_buffers.setdefault(
                        index,
                        {"id": "", "name": "", "arguments_parts": []},
                    )
                    tool_call_id = getattr(tool_call, "id", None)
                    if tool_call_id:
                        buffer["id"] = tool_call_id
                    function = getattr(tool_call, "function", None)
                    if function is None:
                        continue
                    function_name = getattr(function, "name", None)
                    if function_name:
                        buffer["name"] = function_name
                    arguments_delta = getattr(function, "arguments", None)
                    if arguments_delta:
                        buffer["arguments_parts"].append(arguments_delta)

                finish_reason = getattr(choice, "finish_reason", None) or finish_reason
        except Exception as exc:  # noqa: BLE001
            raise self._wrap_error(exc) from exc
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()

        yield ProviderStreamEvent(
            kind="response",
            response=self._build_response(
                text="".join(text_parts),
                raw_tool_calls=[
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "arguments": "".join(item["arguments_parts"]),
                    }
                    for _, item in sorted(tool_buffers.items())
                    if item.get("name")
                ],
                stop_reason=finish_reason,
                usage=stream_usage,
            ),
        )

    def _convert_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools:
            converted.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["input_schema"],
                    },
                }
                )
        return converted

    def _request_kwargs(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system_prompt: str,
        model_override: str | None,
        stream: bool,
    ) -> dict[str, Any]:
        return {
            "model": model_override or self.model,
            "max_tokens": self.max_tokens,
            "messages": self._convert_messages(messages, system_prompt),
            "tools": self._convert_tools(tools),
            "tool_choice": "auto",
            "stream": stream,
        }

    def _convert_messages(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
    ) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for message in messages:
            role = message["role"]
            content_blocks = message.get("content", [])
            if role == "user":
                text_parts = [block["text"] for block in content_blocks if block.get("type") == "text"]
                if text_parts:
                    converted.append({"role": "user", "content": "\n".join(text_parts)})
                for block in content_blocks:
                    if block.get("type") != "tool_result":
                        continue
                    converted.append(
                        {
                            "role": "tool",
                            "tool_call_id": block["tool_use_id"],
                            "content": block["content"],
                        }
                    )
                continue

            if role == "assistant":
                text_parts = [block["text"] for block in content_blocks if block.get("type") == "text"]
                tool_calls = []
                for block in content_blocks:
                    if block.get("type") != "tool_use":
                        continue
                    tool_calls.append(
                        {
                            "id": block["id"],
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(block["input"], ensure_ascii=True),
                            },
                        }
                    )
                converted.append(
                    {
                        "role": "assistant",
                        "content": "\n".join(text_parts) if text_parts else "",
                        **({"tool_calls": tool_calls} if tool_calls else {}),
                    }
                )
                continue

            converted.append(message)
        return converted

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
            return ProviderContextLimitError(f"OpenAI-compatible provider call failed: {message}")
        return ProviderNetworkError(f"OpenAI-compatible provider call failed: {message}")

    def _parse_message_response(
        self,
        message: Any,
        stop_reason: str | None,
        *,
        usage: TokenUsage | None = None,
    ) -> AssistantResponse:
        raw_tool_calls = []
        for tool_call in getattr(message, "tool_calls", None) or []:
            function = getattr(tool_call, "function", None)
            if function is None:
                continue
            raw_tool_calls.append(
                {
                    "id": getattr(tool_call, "id", "") or "",
                    "name": getattr(function, "name", "") or "",
                    "arguments": getattr(function, "arguments", "") or "{}",
                }
            )
        return self._build_response(
            text=(getattr(message, "content", None) or ""),
            raw_tool_calls=raw_tool_calls,
            stop_reason=stop_reason,
            usage=usage,
        )

    def _build_response(
        self,
        *,
        text: str,
        raw_tool_calls: list[dict[str, str]],
        stop_reason: str | None,
        usage: TokenUsage | None = None,
    ) -> AssistantResponse:
        content: list[dict[str, Any]] = []
        tool_calls: list[ToolCall] = []
        stripped_text = text.strip()
        if stripped_text:
            content.append({"type": "text", "text": stripped_text})

        for raw_tool_call in raw_tool_calls:
            tool_name = raw_tool_call["name"]
            tool_input = self._parse_tool_input(raw_tool_call["arguments"])
            content.append(
                {
                    "type": "tool_use",
                    "id": raw_tool_call["id"],
                    "name": tool_name,
                    "input": tool_input,
                }
            )
            tool_calls.append(
                ToolCall(
                    id=raw_tool_call["id"],
                    name=tool_name,
                    input=tool_input,
                )
            )

        return AssistantResponse(
            content=content,
            text=stripped_text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
        )

    def _extract_usage(self, payload: Any) -> TokenUsage | None:
        usage = getattr(payload, "usage", None)
        if usage is None:
            return None
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        if prompt_tokens is None and completion_tokens is None and total_tokens is None:
            return None
        return TokenUsage(
            prompt_tokens=int(prompt_tokens) if prompt_tokens is not None else None,
            completion_tokens=int(completion_tokens) if completion_tokens is not None else None,
            total_tokens=int(total_tokens) if total_tokens is not None else None,
        )

    def _parse_tool_input(self, raw_arguments: str) -> dict[str, Any]:
        try:
            parsed = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError:
            return {"raw_arguments": raw_arguments}
        if isinstance(parsed, dict):
            return parsed
        return {"raw_arguments": raw_arguments}
