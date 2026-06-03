from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.runtime.budget import compute_runtime_budget_state


class RuntimeBudgetTests(unittest.TestCase):
    def test_compute_runtime_budget_state_ok(self) -> None:
        payload = compute_runtime_budget_state(
            context_tokens_estimated=100,
            context_percentage=10.0,
            message_count=2,
            message_limit=8,
            context_summary_chars=0,
            context_summary_limit=1000,
            warning_message_threshold=6,
            warning_summary_threshold=750,
            auto_summary_threshold=900,
            warning_context_percentage=70.0,
            auto_context_percentage=85.0,
            would_compact=True,
            last_turn_token_count=None,
            last_turn_token_source=None,
            provider_usage_seen=False,
        ).to_payload()

        self.assertEqual(payload["budget_state"], "ok")
        self.assertFalse(payload["should_warn"])
        self.assertFalse(payload["should_compact"])
        self.assertFalse(payload["should_stop"])

    def test_compute_runtime_budget_state_warning(self) -> None:
        payload = compute_runtime_budget_state(
            context_tokens_estimated=100,
            context_percentage=10.0,
            message_count=6,
            message_limit=8,
            context_summary_chars=0,
            context_summary_limit=1000,
            warning_message_threshold=6,
            warning_summary_threshold=750,
            auto_summary_threshold=900,
            warning_context_percentage=70.0,
            auto_context_percentage=85.0,
            would_compact=True,
            last_turn_token_count=12,
            last_turn_token_source="estimated",
            provider_usage_seen=False,
        ).to_payload()

        self.assertEqual(payload["budget_state"], "warning")
        self.assertTrue(payload["should_warn"])
        self.assertIn("message count 6 >= warning threshold 6", str(payload["budget_reason"]))
        self.assertEqual(payload["last_turn_token_source"], "estimated")

    def test_compute_runtime_budget_state_compact_needed(self) -> None:
        payload = compute_runtime_budget_state(
            context_tokens_estimated=100,
            context_percentage=10.0,
            message_count=9,
            message_limit=8,
            context_summary_chars=0,
            context_summary_limit=1000,
            warning_message_threshold=6,
            warning_summary_threshold=750,
            auto_summary_threshold=900,
            warning_context_percentage=70.0,
            auto_context_percentage=85.0,
            would_compact=True,
            last_turn_token_count=18,
            last_turn_token_source="provider",
            provider_usage_seen=True,
        ).to_payload()

        self.assertEqual(payload["budget_state"], "compact_needed")
        self.assertTrue(payload["should_compact"])
        self.assertFalse(payload["should_stop"])
        self.assertEqual(payload["last_turn_token_source"], "provider")
        self.assertTrue(payload["provider_usage_seen"])

    def test_compute_runtime_budget_state_hard_stop_without_compaction_path(self) -> None:
        payload = compute_runtime_budget_state(
            context_tokens_estimated=100,
            context_percentage=90.0,
            message_count=1,
            message_limit=8,
            context_summary_chars=950,
            context_summary_limit=1000,
            warning_message_threshold=6,
            warning_summary_threshold=750,
            auto_summary_threshold=900,
            warning_context_percentage=70.0,
            auto_context_percentage=85.0,
            would_compact=False,
            last_turn_token_count=22,
            last_turn_token_source="provider",
            provider_usage_seen=True,
        ).to_payload()

        self.assertEqual(payload["budget_state"], "hard_stop")
        self.assertTrue(payload["should_stop"])
        self.assertFalse(payload["should_compact"])


if __name__ == "__main__":
    unittest.main()
