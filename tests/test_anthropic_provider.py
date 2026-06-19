from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.providers.anthropic import AnthropicProvider
from claudecode_py.runtime.provider_cache import ProviderPromptCachePlan


class _FakeStream:
    def __init__(self) -> None:
        self.text_stream = iter(["Hel", "lo"])
        self._final = SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="Hello"),
                SimpleNamespace(type="tool_use", id="tool-1", name="read_file", input={"path": "README.md"}),
            ],
            stop_reason="tool_use",
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def get_final_message(self):
        return self._final


class _FakeCacheStatusError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class AnthropicProviderStreamingTests(unittest.TestCase):
    def test_create_message_uses_cache_hinted_system_blocks_and_tool_overlays(self) -> None:
        provider = AnthropicProvider(
            model="claude-test",
            max_tokens=256,
            api_key="k",
        )

        class FakeMessages:
            def __init__(self) -> None:
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="done")],
                    stop_reason="end_turn",
                    usage=None,
                )

        fake_messages = FakeMessages()
        provider._client = SimpleNamespace(messages=fake_messages)
        cache_plan = ProviderPromptCachePlan(
            system_prompt="system",
            tools=[
                {
                    "name": "read_file",
                    "description": "Read",
                    "input_schema": {"type": "object"},
                    "cache_control": {"type": "ephemeral", "scope": "org"},
                }
            ],
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            cache_hints=(),
            provider_cache_supported=True,
            provider_cache_mode="provider_hinted",
            provider_cache_summary="stable prefix preserved",
            provider_cache_provider="anthropic",
            system_prompt_blocks=(
                {
                    "type": "text",
                    "text": "system",
                    "cache_control": {"type": "ephemeral", "scope": "org"},
                },
            ),
        )

        response = provider.create_message(
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            tools=[{"name": "read_file", "description": "Read", "input_schema": {"type": "object"}}],
            system_prompt="system",
            cache_plan=cache_plan,
        )

        self.assertEqual(response.text, "done")
        self.assertIsInstance(fake_messages.calls[0]["system"], list)
        self.assertEqual(
            fake_messages.calls[0]["system"][0]["cache_control"],
            {"type": "ephemeral", "scope": "org"},
        )
        self.assertEqual(
            fake_messages.calls[0]["tools"][0]["cache_control"],
            {"type": "ephemeral", "scope": "org"},
        )

    def test_create_message_applies_model_and_effort_overrides(self) -> None:
        provider = AnthropicProvider(
            model="claude-sonnet",
            max_tokens=256,
            api_key="k",
        )

        class FakeMessages:
            def __init__(self) -> None:
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="done")],
                    stop_reason="end_turn",
                    usage=None,
                )

        fake_messages = FakeMessages()
        provider._client = SimpleNamespace(messages=fake_messages)

        response = provider.create_message(
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            tools=[],
            system_prompt="system",
            model_override="claude-opus-4-6",
            effort_override="high",
        )

        self.assertEqual(response.text, "done")
        self.assertEqual(fake_messages.calls[0]["model"], "claude-opus-4-6")
        self.assertEqual(
            fake_messages.calls[0]["extra_headers"],
            {"anthropic-beta": "effort-2025-11-24"},
        )
        self.assertEqual(
            fake_messages.calls[0]["extra_body"],
            {"output_config": {"effort": "high"}},
        )

    def test_create_message_falls_back_to_plain_path_when_cache_hints_are_rejected(self) -> None:
        provider = AnthropicProvider(
            model="claude-test",
            max_tokens=256,
            api_key="k",
        )

        class FakeMessages:
            def __init__(self) -> None:
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    raise _FakeCacheStatusError(400, "cache_control extra inputs are not permitted")
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="done")],
                    stop_reason="end_turn",
                    usage=None,
                )

        fake_messages = FakeMessages()
        provider._client = SimpleNamespace(messages=fake_messages)
        cache_plan = ProviderPromptCachePlan(
            system_prompt="system",
            tools=[
                {
                    "name": "read_file",
                    "description": "Read",
                    "input_schema": {"type": "object"},
                    "cache_control": {"type": "ephemeral", "scope": "org"},
                }
            ],
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            cache_hints=(),
            provider_cache_supported=True,
            provider_cache_mode="provider_hinted",
            provider_cache_summary="stable prefix preserved",
            provider_cache_provider="anthropic",
            system_prompt_blocks=(
                {
                    "type": "text",
                    "text": "system",
                    "cache_control": {"type": "ephemeral", "scope": "org"},
                },
            ),
        )

        response = provider.create_message(
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            tools=[{"name": "read_file", "description": "Read", "input_schema": {"type": "object"}}],
            system_prompt="system",
            cache_plan=cache_plan,
        )

        self.assertEqual(response.text, "done")
        self.assertEqual(len(fake_messages.calls), 2)
        self.assertIsInstance(fake_messages.calls[0]["system"], list)
        self.assertEqual(fake_messages.calls[1]["system"], "system")
        self.assertEqual(cache_plan.provider_cache_mode, "diagnostic_only")
        self.assertIn("cache_control", str(cache_plan.provider_cache_fallback_reason))

    def test_stream_message_emits_text_and_final_response(self) -> None:
        provider = AnthropicProvider(
            model="claude-test",
            max_tokens=256,
            api_key="k",
        )

        class FakeMessages:
            def __init__(self) -> None:
                self.calls = []

            def stream(self, **kwargs):
                self.calls.append(kwargs)
                return _FakeStream()

        fake_messages = FakeMessages()
        provider._client = SimpleNamespace(messages=fake_messages)

        events = list(
            provider.stream_message(
                messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                tools=[{"name": "read_file", "description": "Read", "input_schema": {"type": "object"}}],
                system_prompt="system",
            )
        )

        self.assertEqual([event.text for event in events[:-1]], ["Hel", "lo"])
        final = events[-1].response
        assert final is not None
        self.assertEqual(final.text, "Hello")
        self.assertEqual(final.tool_calls[0].name, "read_file")
        self.assertEqual(final.tool_calls[0].input, {"path": "README.md"})
        self.assertEqual(final.stop_reason, "tool_use")
        self.assertEqual(fake_messages.calls[0]["system"], "system")


if __name__ == "__main__":
    unittest.main()
