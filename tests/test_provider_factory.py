from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.providers import AnthropicProvider, OpenAICompatibleProvider, build_provider


class ProviderFactoryTests(unittest.TestCase):
    def test_factory_builds_anthropic_provider(self) -> None:
        provider = build_provider(
            provider="anthropic",
            model="claude-test",
            max_tokens=1000,
            api_key="k",
            base_url=None,
        )
        self.assertIsInstance(provider, AnthropicProvider)
        self.assertTrue(provider.capabilities.supports_tool_calling)
        self.assertTrue(provider.capabilities.supports_streaming)

    def test_factory_builds_openai_compatible_provider(self) -> None:
        provider = build_provider(
            provider="openai-compatible",
            model="gpt-test",
            max_tokens=1000,
            api_key="k",
            base_url="https://example.test/v1",
        )
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertTrue(provider.capabilities.supports_tool_calling)
        self.assertTrue(provider.capabilities.supports_streaming)
        self.assertEqual(provider.capabilities.provider, "openai-compatible")


if __name__ == "__main__":
    unittest.main()
