from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import default_model_for_provider, load_config


class ConfigTests(unittest.TestCase):
    def test_default_model_changes_with_provider(self) -> None:
        self.assertEqual(default_model_for_provider("anthropic"), "claude-3-7-sonnet-latest")
        self.assertEqual(default_model_for_provider("openai-compatible"), "gpt-4.1-mini")

    def test_load_config_sets_openai_provider_fields(self) -> None:
        config = load_config(
            cwd=".",
            provider="openai-compatible",
            api_key="demo-key",
            base_url="https://example.test/v1",
            mcp_config_path="custom_mcp.json",
            max_tool_rounds_per_turn=5,
            provider_max_retries=4,
            provider_retry_base_delay_sec=0.25,
            max_history_messages=99,
            history_keep_last_messages=12,
            max_context_summary_chars=1234,
            interactive=False,
        )
        self.assertEqual(config.provider, "openai-compatible")
        self.assertEqual(config.api_key, "demo-key")
        self.assertEqual(config.base_url, "https://example.test/v1")
        self.assertTrue(str(config.mcp_config_path).endswith("custom_mcp.json"))
        self.assertEqual(config.model, "gpt-4.1-mini")
        self.assertEqual(config.max_tool_rounds_per_turn, 5)
        self.assertEqual(config.provider_max_retries, 4)
        self.assertEqual(config.provider_retry_base_delay_sec, 0.25)
        self.assertEqual(config.max_history_messages, 99)
        self.assertEqual(config.history_keep_last_messages, 12)
        self.assertEqual(config.max_context_summary_chars, 1234)


if __name__ == "__main__":
    unittest.main()
