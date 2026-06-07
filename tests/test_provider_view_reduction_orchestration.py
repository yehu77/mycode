from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.providers.capabilities import ProviderCapabilities
from claudecode_py.runtime.reduction_orchestration import (
    build_provider_view_reduction_orchestration,
)
from claudecode_py.runtime.tool_result_replacement import (
    build_tool_result_reduction_candidates,
    reapply_frozen_tool_result_reductions,
)
from claudecode_py.session import Session
from claudecode_py.state import ToolResultArtifactRecord, ToolResultReplacementRecord


class ProviderViewReductionOrchestrationTests(unittest.TestCase):
    def _session(self, *, max_tokens: int = 20000) -> Session:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                max_tokens=max_tokens,
            )
        )

        class PlannerProvider:
            def __init__(self) -> None:
                self.capabilities = ProviderCapabilities(
                    provider="anthropic",
                    model="planner-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                    supports_prompt_cache_hints=True,
                    supports_system_prompt_cache_blocks=True,
                    supports_tool_schema_cache_hints=True,
                )

        session.provider = PlannerProvider()
        return session

    def test_reapply_frozen_reductions_does_not_create_new_records(self) -> None:
        session = self._session()
        try:
            replacement = ToolResultReplacementRecord(
                tool_use_id="tool-1",
                replacement="Tool result replaced for context budget.\ntool_use_id=tool-1",
                original_size_chars=6000,
                replacement_size_chars=64,
                created_at="2026-06-07T00:00:00+00:00",
                reason="message_budget",
            )
            artifact = ToolResultArtifactRecord(
                tool_use_id="tool-1",
                artifact_path=str(Path(session.config.cwd) / "artifact.txt"),
                content_sha256="abc",
                original_size_chars=6000,
                preview_size_chars=64,
                created_at="2026-06-07T00:00:00+00:00",
                reason="message_budget",
                summary="artifact summary",
            )
            session._record_tool_result_replacement_records((replacement,))
            session._record_tool_result_artifact_records((artifact,))
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "x" * 6000,
                        }
                    ],
                }
            ]
            session.state.messages = list(messages)

            frozen = reapply_frozen_tool_result_reductions(session, messages)

            self.assertEqual(frozen.reapplied_count, 1)
            self.assertEqual(frozen.artifact_reuse_count, 1)
            self.assertIn(
                "Tool result replaced for context budget.",
                frozen.messages[0]["content"][0]["content"],
            )
            self.assertIn(
                "artifact_status=missing",
                frozen.messages[0]["content"][0]["content"],
            )
            self.assertEqual(len(session.state.tool_result_replacement_records), 1)
            self.assertEqual(len(session.state.tool_result_artifact_records), 1)
        finally:
            session.close()

    def test_candidate_generation_excludes_seen_unreplaced_results(self) -> None:
        session = self._session()
        try:
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "seen-tool",
                            "content": "a" * 7000,
                        }
                    ],
                }
            ]
            session.state.messages = list(messages)
            session.mark_tool_result_ids_seen_from_messages(messages)
            candidate_messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "seen-tool",
                            "content": "a" * 7000,
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "fresh-tool",
                            "content": "b" * 7000,
                        },
                    ],
                }
            ]

            candidates = build_tool_result_reduction_candidates(session, candidate_messages)

            self.assertTrue(candidates)
            self.assertEqual({candidate.tool_use_id for candidate in candidates}, {"fresh-tool"})
        finally:
            session.close()

    def test_orchestration_selects_tail_reduction_without_full_compaction(self) -> None:
        session = self._session(max_tokens=2400)
        try:
            raw_messages = [
                {"role": "user", "content": [{"type": "text", "text": "stable-prefix"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "stable-answer"}]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "fresh-tool",
                            "content": "x" * 7000,
                        }
                    ],
                },
            ]

            orchestration = build_provider_view_reduction_orchestration(
                session,
                raw_messages=raw_messages,
            )

            self.assertTrue(orchestration.available_selectable_candidates)
            self.assertTrue(orchestration.chosen_selectable_candidates)
            self.assertFalse(orchestration.requires_full_compaction)
            self.assertEqual(orchestration.final_cache_plan.orchestration_mode, "selected")
            self.assertIn(
                orchestration.final_cache_plan.orchestration_reason,
                {"artifact_indirection_active", "microcompact_on_tail"},
            )
            self.assertGreaterEqual(
                orchestration.final_cache_plan.orchestration_selected_candidate_count,
                1,
            )
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
