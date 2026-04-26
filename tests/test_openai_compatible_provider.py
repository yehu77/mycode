from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.providers.openai_compatible import OpenAICompatibleProvider


def _chunk(*, content: str = "", tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def _tool_call_delta(*, index: int, id: str | None = None, name: str | None = None, arguments: str | None = None):
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=id, function=function)


class OpenAICompatibleProviderStreamingTests(unittest.TestCase):
    def test_stream_message_builds_final_response(self) -> None:
        provider = OpenAICompatibleProvider(
            model="gpt-test",
            max_tokens=256,
            api_key="k",
            base_url="https://example.test/v1",
        )

        class FakeCompletions:
            def __init__(self) -> None:
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return iter(
                    [
                        _chunk(content="Hel"),
                        _chunk(content="lo"),
                        _chunk(
                            tool_calls=[
                                _tool_call_delta(index=0, id="call-1", name="read_file", arguments='{"path":"'),
                            ]
                        ),
                        _chunk(
                            tool_calls=[
                                _tool_call_delta(index=0, arguments='README.md"}'),
                            ],
                            finish_reason="tool_calls",
                        ),
                    ]
                )

        fake_completions = FakeCompletions()
        provider._client = SimpleNamespace(chat=SimpleNamespace(completions=fake_completions))

        events = list(
            provider.stream_message(
                messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                tools=[
                    {
                        "name": "read_file",
                        "description": "Read a file",
                        "input_schema": {"type": "object", "properties": {}},
                    }
                ],
                system_prompt="system",
            )
        )

        self.assertEqual([event.text for event in events[:-1]], ["Hel", "lo"])
        final = events[-1].response
        assert final is not None
        self.assertEqual(final.text, "Hello")
        self.assertEqual(len(final.tool_calls), 1)
        self.assertEqual(final.tool_calls[0].name, "read_file")
        self.assertEqual(final.tool_calls[0].input, {"path": "README.md"})
        self.assertEqual(final.stop_reason, "tool_calls")
        self.assertTrue(fake_completions.calls[0]["stream"])


if __name__ == "__main__":
    unittest.main()
