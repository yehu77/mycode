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

    def test_plan_mode_attachments_are_dynamic_provider_view_segments(self) -> None:
        session = self._session()
        try:
            session.enter_plan_mode()
            session.state.needs_plan_mode_reentry_attachment = True
            assembly = session.build_provider_prompt_assembly(
                messages=[{"role": "user", "content": [{"type": "text", "text": "plan"}]}]
            )
            payload = session.record_prompt_prefix_assembly(
                assembly,
                source="test",
                cache_plan=session.build_provider_prompt_cache_plan(assembly),
            )

            self.assertEqual(assembly.prompt_attachment_count, 2)
            self.assertEqual(
                assembly.prompt_attachment_kinds,
                ("plan_mode_reentry", "plan_mode"),
            )
            self.assertIn("## Plan Workflow", assembly.system_prompt)
            self.assertIn("### Phase 1: Initial Understanding", assembly.system_prompt)
            self.assertIn("IN PARALLEL (single message, multiple tool calls)", assembly.system_prompt)
            self.assertIn("Quality over quantity", assembly.system_prompt)
            self.assertIn("### Phase 2: Design", assembly.system_prompt)
            self.assertIn("launch at least 1 Plan agent for most tasks", assembly.system_prompt)
            self.assertIn("### Phase 5: Call ExitPlanMode", assembly.system_prompt)
            self.assertIn("Do not make large assumptions about user intent", assembly.system_prompt)
            self.assertIn("## Re-entering Plan Mode", assembly.system_prompt)
            self.assertIn("Read the existing plan file to understand what was previously planned", assembly.system_prompt)
            self.assertEqual(payload["prompt_prefix_attachment_summary"], "plan_mode_reentry,plan_mode")
            self.assertEqual(payload["prompt_prefix_attachment_mode"], "reentry")
            self.assertEqual(payload["prompt_prefix_attachment_change_reason"], "none")
            self.assertTrue(payload["prompt_prefix_plan_mode_attachment_active"])
            self.assertTrue(payload["prompt_prefix_plan_mode_reentry_attachment_active"])
            self.assertFalse(payload["prompt_prefix_plan_mode_exit_attachment_active"])
            self.assertEqual(session.plan_workflow_mode(), payload["plan_workflow_mode"])
            self.assertEqual(payload["plan_workflow_mode"], "five_phase")
            self.assertEqual(payload["plan_workflow_agent_count"], 1)
            self.assertEqual(payload["plan_workflow_explore_agent_count"], 3)
        finally:
            session.close()

    def test_plan_mode_sparse_cadence_is_deterministic_and_resets_on_reenter(self) -> None:
        session = self._session()
        messages = [{"role": "user", "content": [{"type": "text", "text": "plan"}]}]
        try:
            session.enter_plan_mode()

            assembly1 = session.build_provider_prompt_assembly(messages=messages)
            self.assertIn("## Plan Workflow", assembly1.system_prompt)
            self.assertNotIn("Plan mode still active (see full instructions earlier in conversation).", assembly1.system_prompt)
            payload1 = session.record_prompt_prefix_assembly(
                assembly1,
                source="test",
                cache_plan=None,
            )
            self.assertEqual(payload1["prompt_prefix_attachment_mode"], "full")
            session.record_plan_mode_attachment_cycle(assembly1.prompt_attachments)

            assembly2 = session.build_provider_prompt_assembly(messages=messages)
            self.assertIn("Plan mode still active (see full instructions earlier in conversation).", assembly2.system_prompt)
            self.assertIn("Follow 5-phase workflow.", assembly2.system_prompt)
            payload2 = session.record_prompt_prefix_assembly(
                assembly2,
                source="test",
                cache_plan=None,
            )
            self.assertEqual(payload2["prompt_prefix_attachment_mode"], "sparse")
            self.assertEqual(
                payload2["prompt_prefix_attachment_change_reason"],
                "attachment_mode_change",
            )
            session.record_plan_mode_attachment_cycle(assembly2.prompt_attachments)

            for _ in range(3):
                session.record_plan_mode_attachment_cycle(
                    session.build_provider_prompt_assembly(messages=messages).prompt_attachments
                )

            assembly6 = session.build_provider_prompt_assembly(messages=messages)
            self.assertIn("## Plan Workflow", assembly6.system_prompt)
            self.assertNotIn("Plan mode still active (see full instructions earlier in conversation).", assembly6.system_prompt)

            session.exit_plan_mode()
            session.enter_plan_mode()
            assembly_after_reenter = session.build_provider_prompt_assembly(messages=messages)
            self.assertIn("## Plan Workflow", assembly_after_reenter.system_prompt)
            self.assertNotIn("Plan mode still active (see full instructions earlier in conversation).", assembly_after_reenter.system_prompt)
            self.assertEqual(session.state.plan_mode_attachment_count, 0)
        finally:
            session.close()

    def test_plan_mode_attachment_change_reasons_cover_reentry_and_exit_consumption(self) -> None:
        session = self._session()
        messages = [{"role": "user", "content": [{"type": "text", "text": "plan"}]}]
        try:
            session.enter_plan_mode()
            session.state.needs_plan_mode_reentry_attachment = True

            reentry = session.build_provider_prompt_assembly(messages=messages)
            session.record_prompt_prefix_assembly(
                reentry,
                source="test",
                cache_plan=None,
            )
            session.mark_plan_mode_attachment_consumed("plan_mode_reentry")

            after_reentry = session.build_provider_prompt_assembly(messages=messages)
            payload_after_reentry = session.record_prompt_prefix_assembly(
                after_reentry,
                source="test",
                cache_plan=None,
            )
            self.assertEqual(payload_after_reentry["prompt_prefix_attachment_mode"], "full")
            self.assertEqual(
                payload_after_reentry["prompt_prefix_attachment_change_reason"],
                "reentry_consumed",
            )

            session.exit_plan_mode(approved_plan="## Approved Plan\n- implement\n")
            exit_followup = session.build_provider_prompt_assembly(messages=messages)
            session.record_prompt_prefix_assembly(
                exit_followup,
                source="test",
                cache_plan=None,
            )
            session.mark_plan_mode_attachment_consumed("plan_mode_exit")

            post_exit = session.build_provider_prompt_assembly(messages=messages)
            payload_post_exit = session.record_prompt_prefix_assembly(
                post_exit,
                source="test",
                cache_plan=None,
            )
            self.assertEqual(payload_post_exit["prompt_prefix_attachment_mode"], "none")
            self.assertEqual(
                payload_post_exit["prompt_prefix_attachment_change_reason"],
                "exit_consumed",
            )
        finally:
            session.close()

    def test_plan_mode_interview_attachment_uses_iterative_workflow(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                plan_mode_interview_phase=True,
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
        try:
            session.enter_plan_mode()
            assembly = session.build_provider_prompt_assembly(
                messages=[{"role": "user", "content": [{"type": "text", "text": "plan"}]}]
            )
            payload = session.record_prompt_prefix_assembly(
                assembly,
                source="test",
                cache_plan=session.build_provider_prompt_cache_plan(assembly),
            )

            self.assertIn("## Iterative Planning Workflow", assembly.system_prompt)
            self.assertIn("### The Loop", assembly.system_prompt)
            self.assertIn("Repeat this cycle until the plan is complete", assembly.system_prompt)
            self.assertIn(
                "After each discovery, immediately capture what you learned. Do not wait until the end.",
                assembly.system_prompt,
            )
            self.assertIn("### First Turn", assembly.system_prompt)
            self.assertIn(
                "Quickly scan a few key files, write a skeleton plan (headers and rough notes), then ask the first round of user questions.",
                assembly.system_prompt,
            )
            self.assertIn("skeleton plan (headers and rough notes)", assembly.system_prompt)
            self.assertIn(
                "limit yourself to a few key files that establish the likely scope, entry points, and reuse paths",
                assembly.system_prompt,
            )
            self.assertIn(
                "The initial plan should stay at skeleton depth: headers and rough notes only, not a finished final plan.",
                assembly.system_prompt,
            )
            self.assertIn("Do not explore exhaustively before engaging the user", assembly.system_prompt)
            self.assertIn(
                "do not try to finish the final plan before asking your first round of questions",
                assembly.system_prompt,
            )
            self.assertIn("### Asking Good Questions", assembly.system_prompt)
            self.assertIn("Prefer multi-question ask_user_question calls", assembly.system_prompt)
            self.assertIn("### When to Converge", assembly.system_prompt)
            self.assertIn("### Ending Your Turn", assembly.system_prompt)
            self.assertIn("Use ExitPlanMode to request plan approval", assembly.system_prompt)
            self.assertIn(
                "Do not ask about plan approval via plain text or ask_user_question.",
                assembly.system_prompt,
            )
            self.assertIn("Do not default to Plan delegation in interview mode", assembly.system_prompt)
            self.assertIn("only use Explore for scoped reconnaissance", assembly.system_prompt)
            self.assertNotIn("### Phase 1: Initial Understanding", assembly.system_prompt)
            self.assertEqual(session.plan_workflow_mode(), payload["plan_workflow_mode"])
            self.assertEqual(payload["plan_workflow_mode"], "interview")
            self.assertEqual(
                payload["plan_workflow_first_turn_contract"],
                "quick_scan_then_skeleton_plan_then_first_question",
            )
            self.assertIn(
                "Quickly scan a few key files",
                payload["plan_workflow_first_turn_summary"],
            )
            self.assertEqual(
                payload["plan_workflow_first_turn_scan_scope"],
                "quickly_scan_a_few_key_files_only",
            )
            self.assertEqual(
                payload["plan_workflow_first_turn_plan_expectation"],
                "write_a_skeleton_plan_with_headers_and_rough_notes_before_questioning",
            )
            self.assertEqual(
                payload["plan_workflow_first_turn_question_timing"],
                "ask_first_round_of_questions_after_skeleton_plan",
            )
            self.assertEqual(
                payload["plan_workflow_first_turn_regression_guard"],
                "do_not_explore_exhaustively_or_finish_the_final_plan_before_first_questions",
            )
            self.assertEqual(
                payload["plan_workflow_plan_update_contract"],
                "incremental_plan_updates_during_discovery",
            )
            self.assertIn(
                "immediately update the plan file",
                payload["plan_workflow_plan_update_summary"],
            )
            self.assertEqual(
                payload["plan_workflow_plan_update_trigger"],
                "update_plan_file_after_each_meaningful_discovery",
            )
            self.assertEqual(
                payload["plan_workflow_plan_update_deferral_guard"],
                "do_not_defer_plan_writing_until_the_end",
            )
            self.assertEqual(
                payload["plan_workflow_turn_exit_contract"],
                "ask_user_question_or_ExitPlanMode_only",
            )
            self.assertEqual(
                payload["plan_workflow_approval_channel"],
                "ExitPlanMode_only",
            )
            self.assertEqual(
                payload["plan_workflow_turn_exit_forbidden_patterns"],
                "no_plain_text_approval_requests_no_plain_text_stop_no_fake_approval_via_ask_user_question",
            )
            self.assertIn(
                "Explore is optional and scoped in interview mode",
                payload["plan_workflow_planning_agent_usage_summary"],
            )
            self.assertEqual(
                payload["plan_workflow_plan_agent_delegation_rule"],
                "do_not_default_to_Plan_agent_in_interview_mode",
            )
        finally:
            session.close()

    def test_interview_plan_mode_sparse_followup_preserves_branch_identity(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                plan_mode_interview_phase=True,
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
        try:
            session.enter_plan_mode()
            first_assembly = session.build_provider_prompt_assembly(
                messages=[{"role": "user", "content": [{"type": "text", "text": "plan"}]}]
            )
            session.record_plan_mode_attachment_cycle(first_assembly.prompt_attachments)

            followup_assembly = session.build_provider_prompt_assembly(
                messages=[{"role": "user", "content": [{"type": "text", "text": "follow up"}]}]
            )
            payload = session.record_prompt_prefix_assembly(
                followup_assembly,
                source="test",
                cache_plan=session.build_provider_prompt_cache_plan(followup_assembly),
            )

            self.assertIn(
                "Plan mode still active (see full instructions earlier in conversation).",
                followup_assembly.system_prompt,
            )
            self.assertIn("Follow iterative workflow", followup_assembly.system_prompt)
            self.assertIn("After each meaningful discovery, immediately update the plan file", followup_assembly.system_prompt)
            self.assertIn(
                "Interview turns may end only by asking the user a clarification question or by calling ExitPlanMode for approval.",
                followup_assembly.system_prompt,
            )
            self.assertIn(
                "Never ask about plan approval via text or ask_user_question.",
                followup_assembly.system_prompt,
            )
            self.assertIn("interview boundary", followup_assembly.system_prompt)
            self.assertNotIn("Follow 5-phase workflow.", followup_assembly.system_prompt)
            self.assertEqual(payload["plan_workflow_mode"], "interview")
            self.assertEqual(payload["plan_workflow_branch_identity"], "interview_branch")
            self.assertEqual(
                payload["plan_workflow_branch_preservation_rule"],
                "preserve_interview_family_across_followup_rejection_and_retry",
            )
            self.assertEqual(payload["prompt_prefix_attachment_summary"], "plan_mode")
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
