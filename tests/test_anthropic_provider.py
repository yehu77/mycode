from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.providers.anthropic import AnthropicProvider


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


class AnthropicProviderStreamingTests(unittest.TestCase):
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
