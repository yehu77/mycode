from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.providers.capabilities import ProviderCapabilities
from claudecode_py.runtime.tool_result_replacement import ToolResultBudgetResult
from claudecode_py.session import Session


def _replacement_result(
    messages: list[dict],
    *,
    microcompact_count: int = 0,
    microcompacted_message_group_indices: tuple[int, ...] = (),
    artifact_count: int = 0,
    replacement_count: int = 0,
) -> ToolResultBudgetResult:
    has_reduction = bool(microcompact_count or replacement_count or artifact_count)
    return ToolResultBudgetResult(
        messages=messages,
        newly_replaced_records=(),
        newly_artifact_records=(),
        reapplied_count=0,
        artifact_reuse_count=0,
        replacement_count=replacement_count,
        artifact_count=artifact_count,
        microcompact_count=microcompact_count,
        replaced_chars_total=4000 if has_reduction else 0,
        artifact_chars_saved=4000 if artifact_count else 0,
        microcompact_chars_saved=4000 if microcompact_count else 0,
        replaced_tokens_total=1000 if has_reduction else 0,
        artifact_tokens_saved=1000 if artifact_count else 0,
        microcompact_tokens_saved=1000 if microcompact_count else 0,
        microcompacted_message_group_indices=microcompacted_message_group_indices,
        budget_reason="message_budget",
    )


class ProviderPrefixPlannerTests(unittest.TestCase):
    def _session(self) -> Session:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
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

    def test_dynamic_tail_only_preserves_planner_signature(self) -> None:
        session = self._session()
        try:
            messages1 = [
                {"role": "user", "content": [{"type": "text", "text": "stable-prefix"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "tail-a"}]},
            ]
            assembly1 = session.build_provider_prompt_assembly(messages=messages1)
            cache_plan1 = session.build_provider_prompt_cache_plan(assembly1)
            payload1 = session.record_prompt_prefix_assembly(
                assembly1,
                source="test",
                cache_plan=cache_plan1,
            )

            messages2 = [
                {"role": "user", "content": [{"type": "text", "text": "stable-prefix"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "tail-b"}]},
            ]
            assembly2 = session.build_provider_prompt_assembly(messages=messages2)
            plan2 = session.build_provider_view_prefix_plan(
                assembly2,
                previous_payload=payload1,
            )
            costed_plan2 = session.build_provider_view_costed_plan(
                assembly2,
                previous_payload=payload1,
            )

            self.assertEqual(plan2.planner_reason, "dynamic_tail_only")
            self.assertEqual(
                plan2.preserved_prefix_signature,
                payload1["prompt_prefix_preserved_signature"],
            )
            self.assertEqual(costed_plan2.final_planner_verdict, "under_budget")
            self.assertEqual(costed_plan2.target_tokens_to_shed, 0)
            self.assertEqual(costed_plan2.remaining_estimated_overage, 0)
        finally:
            session.close()

    def test_tail_microcompact_preserves_cache_hinting_for_prefix_subset(self) -> None:
        session = self._session()
        try:
            messages = [
                {"role": "user", "content": [{"type": "text", "text": "stable-prefix"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "assistant-stable"}]},
                {"role": "user", "content": [{"type": "text", "text": "tail-user"}]},
            ]
            baseline_assembly = session.build_provider_prompt_assembly(messages=messages)
            baseline_cache_plan = session.build_provider_prompt_cache_plan(baseline_assembly)
            baseline_payload = session.record_prompt_prefix_assembly(
                baseline_assembly,
                source="test",
                cache_plan=baseline_cache_plan,
            )

            replacement_result = _replacement_result(
                messages,
                microcompact_count=1,
                microcompacted_message_group_indices=(2,),
            )
            assembly = session.build_provider_prompt_assembly(
                messages=messages,
                replacement_result=replacement_result,
            )
            plan = session.build_provider_view_prefix_plan(
                assembly,
                previous_payload=baseline_payload,
            )
            cache_plan = session.build_provider_prompt_cache_plan(
                assembly,
                previous_payload=baseline_payload,
            )
            costed_plan = session.build_provider_view_costed_plan(
                assembly,
                previous_payload=baseline_payload,
            )

            self.assertEqual(plan.planner_reason, "microcompact_on_tail")
            self.assertEqual(plan.preserved_message_group_count, 2)
            self.assertEqual(plan.downgraded_message_group_count, 1)
            self.assertEqual(cache_plan.provider_cache_mode, "provider_hinted")
            self.assertGreaterEqual(costed_plan.estimated_tokens_shed, 1000)
            self.assertEqual(costed_plan.remaining_estimated_overage, 0)
            self.assertEqual(costed_plan.prefix_damage_score, 2)
            message_prefix_hints = [
                hint for hint in cache_plan.cache_hints if hint.kind == "message_prefix"
            ]
            self.assertEqual(len(message_prefix_hints), 1)
            self.assertIn("preserved message group", message_prefix_hints[0].summary)
        finally:
            session.close()

    def test_stable_prefix_microcompact_downgrades_preserved_message_groups(self) -> None:
        session = self._session()
        try:
            messages = [
                {"role": "user", "content": [{"type": "text", "text": "stable-0"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "stable-1"}]},
                {"role": "user", "content": [{"type": "text", "text": "tail-user"}]},
            ]
            baseline_assembly = session.build_provider_prompt_assembly(messages=messages)
            baseline_cache_plan = session.build_provider_prompt_cache_plan(baseline_assembly)
            baseline_payload = session.record_prompt_prefix_assembly(
                baseline_assembly,
                source="test",
                cache_plan=baseline_cache_plan,
            )

            replacement_result = _replacement_result(
                messages,
                microcompact_count=1,
                microcompacted_message_group_indices=(1,),
            )
            assembly = session.build_provider_prompt_assembly(
                messages=messages,
                replacement_result=replacement_result,
            )
            plan = session.build_provider_view_prefix_plan(
                assembly,
                previous_payload=baseline_payload,
            )
            costed_plan = session.build_provider_view_costed_plan(
                assembly,
                previous_payload=baseline_payload,
            )

            self.assertEqual(plan.planner_reason, "microcompact_in_stable_prefix")
            self.assertEqual(plan.preserved_message_group_count, 1)
            self.assertEqual(plan.downgraded_message_group_count, 1)
            self.assertTrue(session.prompt_prefix_planner_downgraded(plan))
            self.assertEqual(costed_plan.prefix_damage_score, 3)
            self.assertEqual(
                costed_plan.final_planner_verdict,
                "microcompact_in_stable_prefix",
            )
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
