from pathlib import Path
import json
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.models import AssistantResponse, ProviderStreamEvent, TokenUsage, ToolCall
from claudecode_py.providers.capabilities import ProviderCapabilities
from claudecode_py.providers.errors import (
    ProviderCapabilityError,
    ProviderContextLimitError,
    ProviderNetworkError,
    ProviderRateLimitError,
)
from claudecode_py.runtime.events import RuntimeEvent
from claudecode_py.runtime.query_loop import run_query_loop
from claudecode_py.permissions import ApprovalResult, PermissionManager
from claudecode_py.session import ForkedSkillMutationResult, Session
from claudecode_py.state import PlanningArtifact
from claudecode_py.tools.base import ToolContextUpdate


class QueryLoopTests(unittest.TestCase):
    def test_query_loop_retries_retryable_provider_error(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                provider_max_retries=1,
                provider_retry_base_delay_sec=0.0,
            )
        )
        events: list[RuntimeEvent] = []

        class FakeProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                self.calls += 1
                if self.calls == 1:
                    raise ProviderRateLimitError("busy")
                return AssistantResponse(
                    content=[{"type": "text", "text": "done"}],
                    text="done",
                    tool_calls=[],
                )

        session.provider = FakeProvider()
        result = run_query_loop(session, "hello", sink=events.append)
        self.assertEqual(result, "done")
        self.assertEqual(session.provider.calls, 2)
        self.assertTrue(any(event.kind == "provider_retry" for event in events))

    def test_query_loop_compacts_when_message_budget_is_tight(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                max_history_messages=1,
            )
        )

        class FakeProvider:
            def __init__(self) -> None:
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                return AssistantResponse(
                    content=[{"type": "text", "text": "should not happen"}],
                    text="should not happen",
                    tool_calls=[],
                )

        session.provider = FakeProvider()
        result = run_query_loop(session, "hello")
        self.assertEqual(result, "should not happen")
        self.assertIsNotNone(session.state.context_summary)
        self.assertLessEqual(len(session.state.messages), 1)
        self.assertEqual(session.state.history_boundaries[-1].kind, "compact")
        self.assertEqual(session.state.history_boundaries[-1].trigger, "auto")
        self.assertIn("message count", session.state.history_boundaries[-1].trigger_reason or "")

    def test_query_loop_fails_fast_when_provider_lacks_tool_calling(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )

        class FakeProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="text-only-model",
                    supports_tool_calling=False,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                self.calls += 1
                return AssistantResponse(
                    content=[{"type": "text", "text": "should not happen"}],
                    text="should not happen",
                    tool_calls=[],
                )

        session.provider = FakeProvider()
        with self.assertRaises(ProviderCapabilityError):
            run_query_loop(session, "hello")
        self.assertEqual(session.provider.calls, 0)

    def test_query_loop_compacts_history_into_context_summary(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                max_history_messages=4,
                history_keep_last_messages=2,
            )
        )
        session.state.messages = [
            {"role": "user", "content": [{"type": "text", "text": "one"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
            {"role": "user", "content": [{"type": "text", "text": "three"}]},
        ]
        events: list[RuntimeEvent] = []

        class FakeProvider:
            def __init__(self) -> None:
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                return AssistantResponse(
                    content=[{"type": "text", "text": "done"}],
                    text="done",
                    tool_calls=[],
                )

        session.provider = FakeProvider()
        result = run_query_loop(session, "hello", sink=events.append)

        self.assertEqual(result, "done")
        self.assertIsNotNone(session.state.context_summary)
        assert session.state.context_summary is not None
        self.assertIn("Earlier conversation summary", session.state.context_summary)
        self.assertLessEqual(len(session.state.messages), 4)
        self.assertTrue(any(event.kind == "context_compacted" for event in events))

    def test_query_loop_injects_session_checklist_into_provider_prompt(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )
        session.create_checklist_task(
            subject="Inspect runtime",
            description="Inspect session.py",
            active_form="Inspecting runtime",
            status="in_progress",
        )

        class FakeProvider:
            def __init__(self) -> None:
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )
                self.system_prompt = ""
                self.user_text = ""

            def create_message(self, *, messages, tools, system_prompt):
                self.system_prompt = system_prompt
                self.user_text = str(messages[-1]["content"][0]["text"])
                return AssistantResponse(
                    content=[{"type": "text", "text": "done"}],
                    text="done",
                    tool_calls=[],
                )

        provider = FakeProvider()
        session.provider = provider

        result = run_query_loop(session, "Fix the runtime")

        self.assertEqual(result, "done")
        self.assertIn("Session checklist guidance:", provider.system_prompt)
        self.assertIn("Current session checklist:", provider.system_prompt)
        self.assertIn("subject=Inspect runtime", provider.system_prompt)
        self.assertIn("call session_task_list", provider.system_prompt)
        self.assertIn("call session_task_get", provider.system_prompt)
        self.assertIn("Session checklist to treat as active execution context:", provider.user_text)
        self.assertIn("Call session_task_list before creating new checklist tasks", provider.user_text)
        self.assertIn("Call session_task_get before updating a specific checklist task", provider.user_text)
        self.assertIn("Current user request:", provider.user_text)
        self.assertIn("Fix the runtime", provider.user_text)

    def test_query_loop_injects_recent_checklist_duplicate_guard_into_provider_prompt(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )
        created = session.create_checklist_task(
            subject="Inspect runtime",
            description="Inspect session.py",
            active_form="Inspecting runtime",
            status="in_progress",
        )
        duplicate = session.create_checklist_task(
            subject="Inspect runtime",
            description="Inspect session.py",
            active_form="Inspecting runtime",
        )
        self.assertFalse(duplicate["created"])

        class FakeProvider:
            def __init__(self) -> None:
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )
                self.system_prompt = ""
                self.user_text = ""

            def create_message(self, *, messages, tools, system_prompt):
                self.system_prompt = system_prompt
                self.user_text = str(messages[-1]["content"][0]["text"])
                return AssistantResponse(
                    content=[{"type": "text", "text": "done"}],
                    text="done",
                    tool_calls=[],
                )

        provider = FakeProvider()
        session.provider = provider

        result = run_query_loop(session, "Continue the runtime task")

        self.assertEqual(result, "done")
        self.assertIn("Recent checklist duplicate guard:", provider.system_prompt)
        self.assertIn(f"matched_task_id={created['id']}", provider.system_prompt)
        self.assertIn(
            f"recommended_action=Call session_task_get for task {created['id']}, then use session_task_update to continue or revise it.",
            provider.system_prompt,
        )
        self.assertIn("Use session_task_get", provider.user_text)
        self.assertIn("do not create another checklist task", provider.user_text)

    def test_query_loop_enter_plan_mode_switches_tool_surface_next_call(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_query_loop_enter_plan_mode"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False), persist_transcript=False)
        session.permission_manager = PermissionManager(
            interactive=True,
            approval_handler=lambda _request: ApprovalResult(decision="allow", scope="once"),
        )

        class FakeProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.first_tools: list[str] = []
                self.second_tools: list[str] = []
                self.second_system_prompt = ""
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages
                self.calls += 1
                tool_names = [str(tool.get("name")) for tool in tools]
                if self.calls == 1:
                    self.first_tools = tool_names
                    return AssistantResponse(
                        content=[
                            {
                                "type": "tool_use",
                                "id": "plan-1",
                                "name": "EnterPlanMode",
                                "input": {},
                            }
                        ],
                        text="",
                        tool_calls=[ToolCall(id="plan-1", name="EnterPlanMode", input={})],
                    )
                self.second_tools = tool_names
                self.second_system_prompt = system_prompt
                return AssistantResponse(
                    content=[{"type": "text", "text": "plan mode active"}],
                    text="plan mode active",
                    tool_calls=[],
                )

        provider = FakeProvider()
        session.provider = provider

        try:
            result = run_query_loop(session, "plan the work")

            self.assertEqual(result, "plan mode active")
            self.assertIn("EnterPlanMode", provider.first_tools)
            self.assertNotIn("ExitPlanMode", provider.first_tools)
            self.assertIn("ExitPlanMode", provider.second_tools)
            self.assertNotIn("EnterPlanMode", provider.second_tools)
            self.assertIn("agent", provider.second_tools)
            self.assertTrue(session.in_plan_mode())
            self.assertTrue(session.get_plan_file_path().exists())
            self.assertIn("## Plan Workflow", provider.second_system_prompt)
            self.assertIn("### Phase 1: Initial Understanding", provider.second_system_prompt)
            self.assertIn("IN PARALLEL (single message, multiple tool calls)", provider.second_system_prompt)
            self.assertIn("launch at least 1 Plan agent for most tasks", provider.second_system_prompt)
            self.assertIn("your turn should only end with either using ask_user_question or calling ExitPlanMode", provider.second_system_prompt)
            prefix_payload = session.prompt_prefix_surface_payload()
            self.assertEqual(prefix_payload["plan_workflow_mode"], "five_phase")
            tool_result_text = str(session.state.messages[-2]["content"][0]["content"])
            self.assertIn("Plan mode enabled.", tool_result_text)
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_query_loop_enter_plan_mode_uses_interview_full_attachment(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_query_loop_enter_plan_mode_interview"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(
            SessionConfig(
                cwd=cwd,
                interactive=False,
                plan_mode_interview_phase=True,
            ),
            persist_transcript=False,
        )
        session.permission_manager = PermissionManager(
            interactive=True,
            approval_handler=lambda _request: ApprovalResult(decision="allow", scope="once"),
        )

        class FakeProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.second_system_prompt = ""
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools
                self.calls += 1
                if self.calls == 1:
                    return AssistantResponse(
                        content=[
                            {
                                "type": "tool_use",
                                "id": "plan-interview-1",
                                "name": "EnterPlanMode",
                                "input": {},
                            }
                        ],
                        text="",
                        tool_calls=[ToolCall(id="plan-interview-1", name="EnterPlanMode", input={})],
                    )
                self.second_system_prompt = system_prompt
                return AssistantResponse(
                    content=[{"type": "text", "text": "interview mode active"}],
                    text="interview mode active",
                    tool_calls=[],
                )

        provider = FakeProvider()
        session.provider = provider

        try:
            result = run_query_loop(session, "plan the work")

            self.assertEqual(result, "interview mode active")
            self.assertIn("## Iterative Planning Workflow", provider.second_system_prompt)
            self.assertIn("### The Loop", provider.second_system_prompt)
            self.assertIn("Repeat this cycle until the plan is complete", provider.second_system_prompt)
            self.assertIn(
                "After each discovery, immediately capture what you learned. Do not wait until the end.",
                provider.second_system_prompt,
            )
            self.assertIn("### First Turn", provider.second_system_prompt)
            self.assertIn(
                "Quickly scan a few key files, write a skeleton plan (headers and rough notes), then ask the first round of user questions.",
                provider.second_system_prompt,
            )
            self.assertIn("skeleton plan (headers and rough notes)", provider.second_system_prompt)
            self.assertIn(
                "The initial plan should stay at skeleton depth: headers and rough notes only, not a finished final plan.",
                provider.second_system_prompt,
            )
            self.assertIn("### Asking Good Questions", provider.second_system_prompt)
            self.assertIn("### When to Converge", provider.second_system_prompt)
            self.assertIn("### Ending Your Turn", provider.second_system_prompt)
            self.assertIn(
                "do not try to finish the final plan before asking your first round of questions",
                provider.second_system_prompt,
            )
            self.assertIn(
                "Do not ask about plan approval via plain text or ask_user_question.",
                provider.second_system_prompt,
            )
            self.assertNotIn("### Phase 1: Initial Understanding", provider.second_system_prompt)
            prefix_payload = session.prompt_prefix_surface_payload()
            self.assertEqual(prefix_payload["plan_workflow_mode"], "interview")
            self.assertEqual(
                prefix_payload["plan_workflow_first_turn_contract"],
                "quick_scan_then_skeleton_plan_then_first_question",
            )
            self.assertEqual(
                prefix_payload["plan_workflow_plan_update_trigger"],
                "update_plan_file_after_each_meaningful_discovery",
            )
            self.assertEqual(
                prefix_payload["plan_workflow_approval_channel"],
                "ExitPlanMode_only",
            )
            self.assertEqual(
                prefix_payload["plan_workflow_plan_agent_delegation_rule"],
                "do_not_default_to_Plan_agent_in_interview_mode",
            )
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_query_loop_exit_plan_mode_rejected_stays_in_interview_branch(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_query_loop_exit_plan_mode_rejected_interview"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(
            SessionConfig(
                cwd=cwd,
                interactive=False,
                plan_mode_interview_phase=True,
            ),
            persist_transcript=False,
        )
        session.enter_plan_mode()
        session.get_plan_file_path().write_text(
            "## Plan\n- sketch approach\n- ask user\n",
            encoding="utf-8",
        )

        def approval_handler(request):
            del request
            return ApprovalResult(decision="deny", scope="once")

        session.permission_manager = PermissionManager(
            interactive=True,
            approval_handler=approval_handler,
        )

        class FakeProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.second_system_prompt = ""
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages
                self.calls += 1
                tool_names = [str(tool.get("name")) for tool in tools]
                if self.calls == 1:
                    self.first_tools = tool_names
                    return AssistantResponse(
                        content=[
                            {
                                "type": "tool_use",
                                "id": "plan-exit-interview-reject-1",
                                "name": "ExitPlanMode",
                                "input": {},
                            }
                        ],
                        text="",
                        tool_calls=[
                            ToolCall(
                                id="plan-exit-interview-reject-1",
                                name="ExitPlanMode",
                                input={},
                            )
                        ],
                    )
                self.second_tools = tool_names
                self.second_system_prompt = system_prompt
                return AssistantResponse(
                    content=[{"type": "text", "text": "revise the interview plan"}],
                    text="revise the interview plan",
                    tool_calls=[],
                )

        provider = FakeProvider()
        session.provider = provider

        try:
            result = run_query_loop(session, "continue")

            self.assertEqual(result, "revise the interview plan")
            self.assertTrue(session.in_plan_mode())
            self.assertIn("ExitPlanMode", provider.first_tools)
            self.assertIn("ExitPlanMode", provider.second_tools)
            self.assertNotIn("EnterPlanMode", provider.second_tools)
            self.assertIn("Plan mode still active", provider.second_system_prompt)
            self.assertIn("Follow iterative workflow", provider.second_system_prompt)
            self.assertIn(
                "After each meaningful discovery, immediately update the plan file",
                provider.second_system_prompt,
            )
            self.assertIn(
                "Interview turns may end only by asking the user a clarification question or by calling ExitPlanMode for approval.",
                provider.second_system_prompt,
            )
            self.assertIn("interview boundary", provider.second_system_prompt)
            self.assertNotIn("Follow 5-phase workflow.", provider.second_system_prompt)
            prefix_payload = session.prompt_prefix_surface_payload()
            self.assertEqual(prefix_payload["plan_workflow_mode"], "interview")
            self.assertEqual(prefix_payload["plan_workflow_branch_identity"], "interview_branch")
            self.assertEqual(
                prefix_payload["plan_workflow_branch_preservation_rule"],
                "preserve_interview_family_across_followup_rejection_and_retry",
            )
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_query_loop_exit_plan_mode_approved_continues_same_turn(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_query_loop_exit_plan_mode_approved"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False), persist_transcript=False)
        session.enter_plan_mode()
        session.get_plan_file_path().write_text(
            "## Plan\n- inspect runtime\n- implement changes\n",
            encoding="utf-8",
        )
        approval_requests = []

        def approval_handler(request):
            approval_requests.append(request)
            return ApprovalResult(decision="allow", scope="once")

        session.permission_manager = PermissionManager(
            interactive=True,
            approval_handler=approval_handler,
        )

        class FakeProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.first_tools: list[str] = []
                self.second_tools: list[str] = []
                self.second_messages = []
                self.second_system_prompt = ""
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                self.calls += 1
                tool_names = [str(tool.get("name")) for tool in tools]
                if self.calls == 1:
                    self.first_tools = tool_names
                    return AssistantResponse(
                        content=[
                            {
                                "type": "tool_use",
                                "id": "plan-exit-1",
                                "name": "ExitPlanMode",
                                "input": {},
                            }
                        ],
                        text="",
                        tool_calls=[ToolCall(id="plan-exit-1", name="ExitPlanMode", input={})],
                    )
                self.second_tools = tool_names
                self.second_messages = list(messages)
                self.second_system_prompt = system_prompt
                return AssistantResponse(
                    content=[{"type": "text", "text": "implementation begins"}],
                    text="implementation begins",
                    tool_calls=[],
                )

        provider = FakeProvider()
        session.provider = provider

        try:
            result = run_query_loop(session, "continue")

            self.assertEqual(result, "implementation begins")
            self.assertIn("ExitPlanMode", provider.first_tools)
            self.assertNotIn("EnterPlanMode", provider.first_tools)
            self.assertIn("EnterPlanMode", provider.second_tools)
            self.assertNotIn("ExitPlanMode", provider.second_tools)
            self.assertFalse(session.in_plan_mode())
            self.assertEqual(len(approval_requests), 1)
            request = approval_requests[0]
            self.assertEqual(request.tool_name, "ExitPlanMode")
            self.assertTrue(str(request.approval_key).startswith("exit_plan_mode:"))
            self.assertIn("restore_mode: default", request.details)
            self.assertIn("## Plan", request.details)
            self.assertIn("## Exited Plan Mode", provider.second_system_prompt)
            self.assertIn("Runtime mode is now restored to `default`", provider.second_system_prompt)
            self.assertIn("Approved plan:", provider.second_system_prompt)
            tool_result_block = provider.second_messages[-1]["content"][0]
            self.assertFalse(tool_result_block["is_error"])
            self.assertIn("## Approved Plan:", str(tool_result_block["content"]))
            self.assertIn("inspect runtime", str(tool_result_block["content"]))
            self.assertFalse(session.state.needs_plan_mode_exit_attachment)
            self.assertIsNone(session.state.plan_mode_exit_approved_plan)
            self.assertIsNone(session.state.plan_mode_exit_restored_mode)
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_query_loop_exit_plan_mode_attachment_survives_failed_followup_provider_call(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_query_loop_exit_plan_mode_retry"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(
            SessionConfig(cwd=cwd, interactive=False, provider_max_retries=0),
            persist_transcript=False,
        )
        session.enter_plan_mode()
        session.get_plan_file_path().write_text(
            "## Plan\n- inspect runtime\n- implement changes\n",
            encoding="utf-8",
        )
        session.permission_manager = PermissionManager(
            interactive=True,
            approval_handler=lambda request: ApprovalResult(decision="allow", scope="once"),
        )

        class RetryProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.prompts: list[str] = []
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools
                self.calls += 1
                self.prompts.append(system_prompt)
                if self.calls == 1:
                    return AssistantResponse(
                        content=[
                            {
                                "type": "tool_use",
                                "id": "plan-exit-retry-1",
                                "name": "ExitPlanMode",
                                "input": {},
                            }
                        ],
                        text="",
                        tool_calls=[
                            ToolCall(id="plan-exit-retry-1", name="ExitPlanMode", input={})
                        ],
                    )
                raise ProviderNetworkError("network down after exit")

        provider = RetryProvider()
        session.provider = provider

        class RecoveryProvider:
            def __init__(self) -> None:
                self.prompts: list[str] = []
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools
                self.prompts.append(system_prompt)
                return AssistantResponse(
                    content=[{"type": "text", "text": "implementation begins"}],
                    text="implementation begins",
                    tool_calls=[],
                )

        try:
            with self.assertRaises(ProviderNetworkError):
                run_query_loop(session, "continue")

            self.assertFalse(session.in_plan_mode())
            self.assertTrue(session.state.needs_plan_mode_exit_attachment)
            self.assertEqual(session.state.plan_mode_exit_approved_plan, "## Plan\n- inspect runtime\n- implement changes")
            self.assertEqual(session.state.plan_mode_exit_restored_mode, "default")
            retry_preview = session.build_provider_prompt_assembly(
                messages=[{"role": "user", "content": [{"type": "text", "text": "continue"}]}]
            )
            session.record_prompt_prefix_assembly(
                retry_preview,
                source="test",
                cache_plan=None,
            )
            retry_prefix = session.prompt_prefix_surface_payload()
            retry_status = session.status_surface_payload()
            self.assertEqual(retry_prefix["plan_instruction_state"], "exit_followup")
            self.assertEqual(retry_prefix["prompt_prefix_attachment_mode"], "exit")
            self.assertEqual(retry_status["status_plan_instruction_state"], "exit_followup")
            self.assertEqual(
                retry_status["status_plan_instruction_attachment_mode"],
                "exit",
            )
            self.assertIn("plan attachment mode: exit", session.describe_context())
            self.assertIn(
                "plan attachment state: exit_followup mode=exit reentry=no exit=yes",
                session.describe_status(section="workflow"),
            )

            session.get_plan_file_path().write_text(
                "## Plan\n- CHANGED AFTER APPROVAL\n",
                encoding="utf-8",
            )
            recovery = RecoveryProvider()
            session.provider = recovery
            result = run_query_loop(session, "continue")

            self.assertEqual(result, "implementation begins")
            self.assertIn("## Exited Plan Mode", recovery.prompts[0])
            self.assertIn("## Plan\n- inspect runtime\n- implement changes", recovery.prompts[0])
            self.assertNotIn("CHANGED AFTER APPROVAL", recovery.prompts[0])
            self.assertFalse(session.state.needs_plan_mode_exit_attachment)
            self.assertIsNone(session.state.plan_mode_exit_approved_plan)
            self.assertIsNone(session.state.plan_mode_exit_restored_mode)
            self.assertEqual(
                session.prompt_prefix_surface_payload()["prompt_prefix_attachment_mode"],
                "none",
            )
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_query_loop_exit_plan_mode_attachment_does_not_repeat_after_second_success(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_query_loop_exit_plan_mode_once"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False), persist_transcript=False)
        session.enter_plan_mode()
        session.get_plan_file_path().write_text(
            "## Plan\n- inspect runtime\n- implement changes\n",
            encoding="utf-8",
        )
        session.permission_manager = PermissionManager(
            interactive=True,
            approval_handler=lambda request: ApprovalResult(decision="allow", scope="once"),
        )

        class TwoTurnProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.prompts: list[str] = []
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools
                self.calls += 1
                self.prompts.append(system_prompt)
                if self.calls == 1:
                    return AssistantResponse(
                        content=[
                            {
                                "type": "tool_use",
                                "id": "plan-exit-once-1",
                                "name": "ExitPlanMode",
                                "input": {},
                            }
                        ],
                        text="",
                        tool_calls=[
                            ToolCall(id="plan-exit-once-1", name="ExitPlanMode", input={})
                        ],
                    )
                return AssistantResponse(
                    content=[{"type": "text", "text": "implementation begins"}],
                    text="implementation begins",
                    tool_calls=[],
                )

        provider = TwoTurnProvider()
        session.provider = provider

        try:
            first = run_query_loop(session, "continue")
            second = run_query_loop(session, "continue again")

            self.assertEqual(first, "implementation begins")
            self.assertEqual(second, "implementation begins")
            self.assertIn("## Exited Plan Mode", provider.prompts[1])
            self.assertNotIn("## Exited Plan Mode", provider.prompts[2])
            self.assertFalse(session.state.needs_plan_mode_exit_attachment)
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_query_loop_exit_plan_mode_rejected_stays_in_plan_mode(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_query_loop_exit_plan_mode_rejected"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False), persist_transcript=False)
        session.enter_plan_mode()
        session.get_plan_file_path().write_text(
            "## Plan\n- inspect runtime\n- refine plan\n",
            encoding="utf-8",
        )
        approval_requests = []

        def approval_handler(request):
            approval_requests.append(request)
            return ApprovalResult(decision="deny", scope="once")

        session.permission_manager = PermissionManager(
            interactive=True,
            approval_handler=approval_handler,
        )

        class FakeProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.first_tools: list[str] = []
                self.second_tools: list[str] = []
                self.second_messages = []
                self.second_system_prompt = ""
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                self.calls += 1
                tool_names = [str(tool.get("name")) for tool in tools]
                if self.calls == 1:
                    self.first_tools = tool_names
                    return AssistantResponse(
                        content=[
                            {
                                "type": "tool_use",
                                "id": "plan-exit-2",
                                "name": "ExitPlanMode",
                                "input": {},
                            }
                        ],
                        text="",
                        tool_calls=[ToolCall(id="plan-exit-2", name="ExitPlanMode", input={})],
                    )
                self.second_tools = tool_names
                self.second_messages = list(messages)
                self.second_system_prompt = system_prompt
                return AssistantResponse(
                    content=[{"type": "text", "text": "revise the plan"}],
                    text="revise the plan",
                    tool_calls=[],
                )

        provider = FakeProvider()
        session.provider = provider

        try:
            result = run_query_loop(session, "continue")

            self.assertEqual(result, "revise the plan")
            self.assertTrue(session.in_plan_mode())
            self.assertEqual(len(approval_requests), 1)
            self.assertIn("ExitPlanMode", provider.first_tools)
            self.assertNotIn("EnterPlanMode", provider.first_tools)
            self.assertIn("ExitPlanMode", provider.second_tools)
            self.assertNotIn("EnterPlanMode", provider.second_tools)
            self.assertIn("Plan mode still active", provider.second_system_prompt)
            self.assertIn("Follow 5-phase workflow.", provider.second_system_prompt)
            self.assertEqual(session.state.plan_mode_attachment_count, 2)
            prefix_payload = session.prompt_prefix_surface_payload()
            status_payload = session.status_surface_payload()
            self.assertEqual(prefix_payload["plan_workflow_mode"], "five_phase")
            self.assertEqual(prefix_payload["plan_instruction_state"], "plan_mode_active")
            self.assertEqual(prefix_payload["prompt_prefix_attachment_mode"], "sparse")
            self.assertEqual(status_payload["status_plan_instruction_state"], "plan_mode_active")
            self.assertEqual(
                status_payload["status_plan_instruction_attachment_mode"],
                "sparse",
            )
            self.assertIn("plan attachment mode: sparse", session.describe_context())
            self.assertIn(
                "plan attachment state: plan_mode_active mode=sparse reentry=no exit=no",
                session.describe_status(section="workflow"),
            )
            next_assembly = session.build_provider_prompt_assembly(
                messages=[{"role": "user", "content": [{"type": "text", "text": "revise"}]}]
            )
            self.assertIn(
                "Plan mode still active (see full instructions earlier in conversation).",
                next_assembly.system_prompt,
            )
            tool_result_block = provider.second_messages[-1]["content"][0]
            self.assertTrue(tool_result_block["is_error"])
            self.assertIn("Rejected plan:", str(tool_result_block["content"]))
            self.assertIn("refine plan", str(tool_result_block["content"]))
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_restored_plan_mode_session_emits_and_consumes_reentry_attachment(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_query_loop_plan_mode_reentry"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        original = Session(SessionConfig(cwd=cwd, interactive=False), persist_transcript=False)
        original.enter_plan_mode()
        original.get_plan_file_path().write_text("# Restored plan\n- continue\n", encoding="utf-8")
        from claudecode_py.storage.transcript import save_transcript
        from claudecode_py.session_factory import SessionFactory

        save_transcript(original.config, original.state)
        session_id = original.state.session_id
        original.close()

        factory = SessionFactory(load_mcp_from_config=False)
        restored, _ = factory.create_or_restore_session(
            SessionConfig(cwd=cwd, interactive=False),
            resume_session_id=session_id,
        )

        class FakeProvider:
            def __init__(self) -> None:
                self.system_prompt = ""
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools
                self.system_prompt = system_prompt
                return AssistantResponse(
                    content=[{"type": "text", "text": "continue planning"}],
                    text="continue planning",
                    tool_calls=[],
                )

        restored.provider = FakeProvider()

        try:
            self.assertTrue(restored.state.needs_plan_mode_reentry_attachment)
            result = run_query_loop(restored, "continue")

            self.assertEqual(result, "continue planning")
            self.assertIn("## Re-entering Plan Mode", restored.provider.system_prompt)
            self.assertIn("Read the existing plan file to understand what was previously planned", restored.provider.system_prompt)
            self.assertIn("always edit the plan file one way or the other before calling ExitPlanMode", restored.provider.system_prompt)
            self.assertIn("## Plan Workflow", restored.provider.system_prompt)
            self.assertIn("### Phase 2: Design", restored.provider.system_prompt)
            self.assertIn("Quality over quantity", restored.provider.system_prompt)
            self.assertEqual(restored.prompt_prefix_surface_payload()["plan_workflow_mode"], "five_phase")
            self.assertFalse(restored.state.needs_plan_mode_reentry_attachment)
        finally:
            restored.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_restored_plan_mode_reentry_attachment_survives_failed_turn_and_retries_once(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_query_loop_plan_mode_reentry_retry"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        original = Session(SessionConfig(cwd=cwd, interactive=False), persist_transcript=False)
        original.enter_plan_mode()
        original.get_plan_file_path().write_text("# Restored plan\n- continue\n", encoding="utf-8")
        from claudecode_py.storage.transcript import save_transcript
        from claudecode_py.session_factory import SessionFactory

        save_transcript(original.config, original.state)
        session_id = original.state.session_id
        original.close()

        factory = SessionFactory(load_mcp_from_config=False)
        restored, _ = factory.create_or_restore_session(
            SessionConfig(cwd=cwd, interactive=False),
            resume_session_id=session_id,
        )

        class RetryProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.prompts: list[str] = []
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools
                self.calls += 1
                self.prompts.append(system_prompt)
                if self.calls == 1:
                    return AssistantResponse(
                        content=[
                            {
                                "type": "tool_use",
                                "id": "reentry-fail-1",
                                "name": "ask_user_question",
                                "input": {"question": "Need context?"},
                            }
                        ],
                        text="",
                        tool_calls=[
                            ToolCall(
                                id="reentry-fail-1",
                                name="ask_user_question",
                                input={"question": "Need context?"},
                            )
                        ],
                    )
                return AssistantResponse(
                    content=[{"type": "text", "text": "continue planning"}],
                    text="continue planning",
                    tool_calls=[],
                )

        def boom(_tool_calls, _ctx, *, sink=None):
            del sink
            raise RuntimeError("tool execution failed")

        provider = RetryProvider()
        restored.provider = provider
        original_execute = restored.execute_tool_calls
        try:
            restored.execute_tool_calls = boom  # type: ignore[method-assign]
            with self.assertRaisesRegex(RuntimeError, "tool execution failed"):
                run_query_loop(restored, "continue")

            self.assertTrue(restored.state.needs_plan_mode_reentry_attachment)
            self.assertIn("## Re-entering Plan Mode", provider.prompts[0])

            restored.execute_tool_calls = original_execute  # type: ignore[method-assign]
            result = run_query_loop(restored, "continue")
            self.assertEqual(result, "continue planning")
            self.assertTrue(any("## Re-entering Plan Mode" in prompt for prompt in provider.prompts[1:]))
            self.assertFalse(restored.state.needs_plan_mode_reentry_attachment)
        finally:
            restored.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_restored_interview_plan_mode_session_preserves_reentry_family(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_query_loop_plan_mode_reentry_interview"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        original = Session(
            SessionConfig(
                cwd=cwd,
                interactive=False,
                plan_mode_interview_phase=True,
            ),
            persist_transcript=False,
        )
        original.enter_plan_mode()
        original.get_plan_file_path().write_text("# Restored interview plan\n- continue\n", encoding="utf-8")
        from claudecode_py.storage.transcript import save_transcript
        from claudecode_py.session_factory import SessionFactory

        save_transcript(original.config, original.state)
        session_id = original.state.session_id
        original.close()

        factory = SessionFactory(load_mcp_from_config=False)
        restored, _ = factory.create_or_restore_session(
            SessionConfig(
                cwd=cwd,
                interactive=False,
                plan_mode_interview_phase=True,
            ),
            resume_session_id=session_id,
        )

        class FakeProvider:
            def __init__(self) -> None:
                self.system_prompt = ""
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools
                self.system_prompt = system_prompt
                return AssistantResponse(
                    content=[{"type": "text", "text": "continue interview planning"}],
                    text="continue interview planning",
                    tool_calls=[],
                )

        restored.provider = FakeProvider()

        try:
            self.assertTrue(restored.state.needs_plan_mode_reentry_attachment)
            result = run_query_loop(restored, "continue")

            self.assertEqual(result, "continue interview planning")
            self.assertIn("## Re-entering Plan Mode", restored.provider.system_prompt)
            self.assertIn("## Iterative Planning Workflow", restored.provider.system_prompt)
            self.assertIn("### The Loop", restored.provider.system_prompt)
            self.assertIn(
                "After each discovery, immediately capture what you learned. Do not wait until the end.",
                restored.provider.system_prompt,
            )
            self.assertNotIn("## Plan Workflow", restored.provider.system_prompt)
            self.assertNotIn("### Phase 2: Design", restored.provider.system_prompt)
            payload = restored.prompt_prefix_surface_payload()
            self.assertEqual(payload["plan_workflow_mode"], "interview")
            self.assertEqual(
                payload["plan_workflow_followup_continuity_contract"],
                "sparse_reentry_reject_retry_preserve_interview_family",
            )
            self.assertFalse(restored.state.needs_plan_mode_reentry_attachment)
        finally:
            restored.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_restored_plan_mode_reentry_attachment_does_not_repeat_after_second_success(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_query_loop_plan_mode_reentry_once"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        original = Session(SessionConfig(cwd=cwd, interactive=False), persist_transcript=False)
        original.enter_plan_mode()
        original.get_plan_file_path().write_text("# Restored plan\n- continue\n", encoding="utf-8")
        from claudecode_py.storage.transcript import save_transcript
        from claudecode_py.session_factory import SessionFactory

        save_transcript(original.config, original.state)
        session_id = original.state.session_id
        original.close()

        factory = SessionFactory(load_mcp_from_config=False)
        restored, _ = factory.create_or_restore_session(
            SessionConfig(cwd=cwd, interactive=False),
            resume_session_id=session_id,
        )

        class TwoTurnProvider:
            def __init__(self) -> None:
                self.prompts: list[str] = []
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools
                self.prompts.append(system_prompt)
                return AssistantResponse(
                    content=[{"type": "text", "text": "continue planning"}],
                    text="continue planning",
                    tool_calls=[],
                )

        provider = TwoTurnProvider()
        restored.provider = provider

        try:
            first = run_query_loop(restored, "continue")
            second = run_query_loop(restored, "continue again")

            self.assertEqual(first, "continue planning")
            self.assertEqual(second, "continue planning")
            self.assertIn("## Re-entering Plan Mode", provider.prompts[0])
            self.assertNotIn("## Re-entering Plan Mode", provider.prompts[1])
            self.assertFalse(restored.state.needs_plan_mode_reentry_attachment)
        finally:
            restored.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_restored_plan_mode_session_preserves_sparse_cadence(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_query_loop_plan_mode_reentry_sparse"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        original = Session(SessionConfig(cwd=cwd, interactive=False), persist_transcript=False)
        original.enter_plan_mode()
        original.get_plan_file_path().write_text("# Restored plan\n- continue\n", encoding="utf-8")
        prior_assembly = original.build_provider_prompt_assembly(
            messages=[{"role": "user", "content": [{"type": "text", "text": "plan"}]}]
        )
        original.record_plan_mode_attachment_cycle(prior_assembly.prompt_attachments)
        from claudecode_py.storage.transcript import save_transcript
        from claudecode_py.session_factory import SessionFactory

        save_transcript(original.config, original.state)
        session_id = original.state.session_id
        original.close()

        factory = SessionFactory(load_mcp_from_config=False)
        restored, _ = factory.create_or_restore_session(
            SessionConfig(cwd=cwd, interactive=False),
            resume_session_id=session_id,
        )

        class FakeProvider:
            def __init__(self) -> None:
                self.system_prompt = ""
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools
                self.system_prompt = system_prompt
                return AssistantResponse(
                    content=[{"type": "text", "text": "continue planning"}],
                    text="continue planning",
                    tool_calls=[],
                )

        restored.provider = FakeProvider()

        try:
            self.assertEqual(restored.state.plan_mode_attachment_count, 1)
            result = run_query_loop(restored, "continue")

            self.assertEqual(result, "continue planning")
            self.assertIn("## Re-entering Plan Mode", restored.provider.system_prompt)
            self.assertIn(
                "Plan mode still active (see full instructions earlier in conversation).",
                restored.provider.system_prompt,
            )
            self.assertNotIn("## Plan Workflow", restored.provider.system_prompt)
            self.assertEqual(restored.state.plan_mode_attachment_count, 2)
        finally:
            restored.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_query_loop_rolls_back_failed_turn_state(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                provider_max_retries=0,
            )
        )
        existing_messages = [
            {"role": "user", "content": [{"type": "text", "text": "before"}]},
        ]
        session.state.messages = list(existing_messages)
        session.state.context_summary = "earlier summary"

        class FakeProvider:
            def __init__(self) -> None:
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                raise ProviderNetworkError("network down")

        session.provider = FakeProvider()
        with self.assertRaises(ProviderNetworkError):
            run_query_loop(session, "hello")

        self.assertEqual(session.state.messages, existing_messages)
        self.assertEqual(session.state.context_summary, "earlier summary")

    def test_query_loop_rolls_back_plan_mode_attachment_cadence(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                provider_max_retries=0,
            )
        )
        session.enter_plan_mode()
        prior_assembly = session.build_provider_prompt_assembly(
            messages=[{"role": "user", "content": [{"type": "text", "text": "before"}]}]
        )
        session.record_plan_mode_attachment_cycle(prior_assembly.prompt_attachments)

        class FakeProvider:
            def __init__(self) -> None:
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                return AssistantResponse(
                    content=[
                        {
                            "type": "tool_use",
                            "id": "fail-1",
                            "name": "ask_user_question",
                            "input": {
                                "question": "Need info?",
                            },
                        }
                    ],
                    text="",
                    tool_calls=[ToolCall(id="fail-1", name="ask_user_question", input={"question": "Need info?"})],
                )

        def boom(_tool_calls, _ctx, *, sink=None):
            del sink
            raise RuntimeError("tool execution failed")

        session.provider = FakeProvider()
        session.execute_tool_calls = boom  # type: ignore[method-assign]
        try:
            with self.assertRaisesRegex(RuntimeError, "tool execution failed"):
                run_query_loop(session, "continue planning")

            self.assertEqual(session.state.plan_mode_attachment_count, 1)
            self.assertEqual(session._plan_mode_attachment_count, 1)
            next_assembly = session.build_provider_prompt_assembly(
                messages=[{"role": "user", "content": [{"type": "text", "text": "retry"}]}]
            )
            self.assertIn(
                "Plan mode still active (see full instructions earlier in conversation).",
                next_assembly.system_prompt,
            )
        finally:
            session.close()

    def test_query_loop_retry_preserves_interview_branch_identity(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                provider_max_retries=0,
                plan_mode_interview_phase=True,
            )
        )
        session.enter_plan_mode()
        prior_assembly = session.build_provider_prompt_assembly(
            messages=[{"role": "user", "content": [{"type": "text", "text": "before"}]}]
        )
        session.record_plan_mode_attachment_cycle(prior_assembly.prompt_attachments)

        class FakeProvider:
            def __init__(self) -> None:
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                return AssistantResponse(
                    content=[
                        {
                            "type": "tool_use",
                            "id": "fail-interview-1",
                            "name": "ask_user_question",
                            "input": {
                                "question": "Need info?",
                            },
                        }
                    ],
                    text="",
                    tool_calls=[
                        ToolCall(
                            id="fail-interview-1",
                            name="ask_user_question",
                            input={"question": "Need info?"},
                        )
                    ],
                )

        def boom(_tool_calls, _ctx, *, sink=None):
            del sink
            raise RuntimeError("tool execution failed")

        session.provider = FakeProvider()
        session.execute_tool_calls = boom  # type: ignore[method-assign]
        try:
            with self.assertRaisesRegex(RuntimeError, "tool execution failed"):
                run_query_loop(session, "continue planning")

            self.assertEqual(session.state.plan_mode_attachment_count, 1)
            next_assembly = session.build_provider_prompt_assembly(
                messages=[{"role": "user", "content": [{"type": "text", "text": "retry"}]}]
            )
            payload = session.record_prompt_prefix_assembly(
                next_assembly,
                source="test",
                cache_plan=None,
            )
            self.assertIn(
                "Plan mode still active (see full instructions earlier in conversation).",
                next_assembly.system_prompt,
            )
            self.assertIn("Follow iterative workflow", next_assembly.system_prompt)
            self.assertIn(
                "After each meaningful discovery, immediately update the plan file",
                next_assembly.system_prompt,
            )
            self.assertIn("interview boundary", next_assembly.system_prompt)
            self.assertNotIn("Follow 5-phase workflow.", next_assembly.system_prompt)
            self.assertEqual(payload["plan_workflow_mode"], "interview")
            self.assertEqual(payload["plan_workflow_branch_identity"], "interview_branch")
        finally:
            session.close()

    def test_query_loop_inlines_skill_tool_messages_and_applies_context_overlay(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_query_loop_skill_inline"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        (cwd / ".claude" / "skills" / "ship").mkdir(parents=True)
        (cwd / ".claude" / "skills" / "ship" / "SKILL.md").write_text(
            "---\n"
            "description: Ship release\n"
            "arguments:\n"
            "  - version\n"
            "allowed-tools:\n"
            "  - Read\n"
            "  - Bash(git status:*)\n"
            "model: claude-opus-4-6\n"
            "effort: high\n"
            "---\n\n"
            "Release version: $version\n",
            encoding="utf-8",
        )
        session = Session(
            SessionConfig(
                cwd=cwd,
                interactive=False,
            ),
            persist_transcript=False,
        )
        session.permission_manager.require_approval = lambda request: None
        events: list[RuntimeEvent] = []

        class FakeProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.second_messages = []
                self.second_tools = []
                self.second_model_override = None
                self.second_effort_override = None
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(
                self,
                *,
                messages,
                tools,
                system_prompt,
                model_override=None,
                effort_override=None,
            ):
                del system_prompt
                self.calls += 1
                if self.calls == 1:
                    return AssistantResponse(
                        content=[
                            {
                                "type": "tool_use",
                                "id": "skill-1",
                                "name": "skill",
                                "input": {"skill": "ship", "args": "1.2.3"},
                            }
                        ],
                        text="",
                        tool_calls=[
                            ToolCall(id="skill-1", name="skill", input={"skill": "ship", "args": "1.2.3"})
                        ],
                    )
                self.second_messages = list(messages)
                self.second_tools = [tool["name"] for tool in tools]
                self.second_model_override = model_override
                self.second_effort_override = effort_override
                return AssistantResponse(
                    content=[{"type": "text", "text": "done"}],
                    text="done",
                    tool_calls=[],
                )

        provider = FakeProvider()
        session.provider = provider

        try:
            result = run_query_loop(session, "hello", sink=events.append)

            self.assertEqual(result, "done")
            self.assertEqual(set(provider.second_tools), {"bash", "read_file"})
            injected = provider.second_messages[-1]
            self.assertEqual(injected["source_kind"], "skill_tool_inline")
            self.assertEqual(injected["source_tool_use_id"], "skill-1")
            self.assertEqual(injected["skill_name"], "ship")
            self.assertIn("Release version: 1.2.3", injected["content"][0]["text"])
            self.assertIsNone(session.transient_tool_context_policy())
            self.assertIsNone(session.transient_runtime_override())
            self.assertEqual(provider.second_model_override, "claude-opus-4-6")
            self.assertEqual(provider.second_effort_override, "high")
            self.assertTrue(
                any(event.kind == "skill_tool_inline_messages_applied" for event in events)
            )
            self.assertTrue(any(event.kind == "skill_tool_context_applied" for event in events))
            runtime_progress = session.runtime_progress_surface_payload()
            self.assertIn(
                "ship",
                str(runtime_progress.get("runtime_skill_tool_inline_summary") or ""),
            )
            self.assertIn(
                "read_file,bash",
                str(runtime_progress.get("runtime_skill_tool_context_summary") or ""),
            )
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_query_loop_persists_fork_skill_messages_and_context(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_query_loop_skill_fork"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        (cwd / ".claude" / "skills" / "ship").mkdir(parents=True)
        (cwd / ".claude" / "skills" / "ship" / "SKILL.md").write_text(
            "---\n"
            "description: Ship release\n"
            "context: fork\n"
            "arguments:\n"
            "  - version\n"
            "allowed-tools:\n"
            "  - Read\n"
            "  - Bash(git status)\n"
            "---\n\n"
            "Release version: $version\n",
            encoding="utf-8",
        )
        session = Session(SessionConfig(cwd=cwd, interactive=False), persist_transcript=False)
        session.permission_manager.require_approval = lambda request: None
        events: list[RuntimeEvent] = []

        class FakeProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.second_messages = []
                self.second_tools = []
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del system_prompt
                self.calls += 1
                if self.calls == 1:
                    return AssistantResponse(
                        content=[
                            {
                                "type": "tool_use",
                                "id": "skill-1",
                                "name": "skill",
                                "input": {"skill": "ship", "args": "1.2.3"},
                            }
                        ],
                        text="",
                        tool_calls=[
                            ToolCall(id="skill-1", name="skill", input={"skill": "ship", "args": "1.2.3"})
                        ],
                    )
                self.second_messages = list(messages)
                self.second_tools = [tool["name"] for tool in tools]
                return AssistantResponse(
                    content=[{"type": "text", "text": "done"}],
                    text="done",
                    tool_calls=[],
                )

        provider = FakeProvider()
        session.provider = provider

        try:
            with patch.object(
                session,
                "run_forked_skill_mutation",
                return_value=ForkedSkillMutationResult(
                    result_text="forked result",
                    new_messages=[
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": "child prompt"}],
                            "source_kind": "skill_tool_fork",
                            "source_tool_name": "skill",
                            "source_tool_use_id": "skill-1",
                            "skill_name": "ship",
                            "skill_execution_context": "fork",
                        },
                        {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "child answer"}],
                            "source_kind": "skill_tool_fork",
                            "source_tool_name": "skill",
                            "source_tool_use_id": "skill-1",
                            "skill_name": "ship",
                            "skill_execution_context": "fork",
                        },
                    ],
                    context_update=ToolContextUpdate(
                        allowed_tool_names=("read_file", "bash"),
                        allowed_bash_command_prefixes=("git status",),
                        source="skill_tool_fork",
                        skill_name="ship",
                    ),
                    injected_message_count=2,
                ),
            ):
                result = run_query_loop(session, "hello", sink=events.append)

            self.assertEqual(result, "done")
            self.assertEqual(set(provider.second_tools), {"read_file", "bash"})
            injected = [m for m in provider.second_messages if m.get("source_kind") == "skill_tool_fork"]
            self.assertEqual(len(injected), 2)
            self.assertEqual(injected[0]["source_tool_use_id"], "skill-1")
            self.assertEqual(injected[1]["content"][0]["text"], "child answer")
            self.assertIsNone(session.transient_tool_context_policy())
            self.assertTrue(
                any(event.kind == "skill_tool_fork_messages_applied" for event in events)
            )
            self.assertTrue(any(event.kind == "skill_tool_context_applied" for event in events))
            runtime_progress = session.runtime_progress_surface_payload()
            self.assertIn(
                "ship",
                str(runtime_progress.get("runtime_skill_tool_fork_summary") or ""),
            )
            self.assertIn(
                "read_file,bash",
                str(runtime_progress.get("runtime_skill_tool_context_summary") or ""),
            )
            status_payload = session.status_surface_payload()
            self.assertIn(
                "ship",
                str(status_payload.get("status_runtime_skill_tool_fork_summary") or ""),
            )
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_query_loop_rolls_back_injected_skill_messages_on_failure(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_query_loop_skill_inline_failure"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        (cwd / ".claude" / "skills" / "ship").mkdir(parents=True)
        (cwd / ".claude" / "skills" / "ship" / "SKILL.md").write_text(
            "---\n"
            "description: Ship release\n"
            "arguments:\n"
            "  - version\n"
            "allowed-tools:\n"
            "  - Read\n"
            "model: claude-opus-4-6\n"
            "effort: medium\n"
            "---\n\n"
            "Release version: $version\n",
            encoding="utf-8",
        )
        session = Session(
            SessionConfig(
                cwd=cwd,
                interactive=False,
                provider_max_retries=0,
            ),
            persist_transcript=False,
        )
        session.permission_manager.require_approval = lambda request: None
        starting_messages = [{"role": "user", "content": [{"type": "text", "text": "before"}]}]
        session.state.messages = list(starting_messages)

        class FailingProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                self.calls += 1
                if self.calls == 1:
                    return AssistantResponse(
                        content=[
                            {
                                "type": "tool_use",
                                "id": "skill-1",
                                "name": "skill",
                                "input": {"skill": "ship", "args": "1.2.3"},
                            }
                        ],
                        text="",
                        tool_calls=[
                            ToolCall(id="skill-1", name="skill", input={"skill": "ship", "args": "1.2.3"})
                        ],
                    )
                raise ProviderNetworkError("network down")

        session.provider = FailingProvider()

        try:
            with self.assertRaises(ProviderNetworkError):
                run_query_loop(session, "hello")

            self.assertEqual(session.state.messages, starting_messages)
            self.assertIsNone(session.transient_tool_context_policy())
            self.assertIsNone(session.transient_runtime_override())
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_query_loop_rolls_back_fork_skill_messages_on_failure(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_query_loop_skill_fork_failure"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        (cwd / ".claude" / "skills" / "ship").mkdir(parents=True)
        (cwd / ".claude" / "skills" / "ship" / "SKILL.md").write_text(
            "---\n"
            "description: Ship release\n"
            "context: fork\n"
            "allowed-tools:\n"
            "  - Read\n"
            "---\n\n"
            "Release workflow\n",
            encoding="utf-8",
        )
        session = Session(
            SessionConfig(
                cwd=cwd,
                interactive=False,
                provider_max_retries=0,
            ),
            persist_transcript=False,
        )
        session.permission_manager.require_approval = lambda request: None
        starting_messages = [{"role": "user", "content": [{"type": "text", "text": "before"}]}]
        session.state.messages = list(starting_messages)

        class FailingProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                self.calls += 1
                if self.calls == 1:
                    return AssistantResponse(
                        content=[
                            {
                                "type": "tool_use",
                                "id": "skill-1",
                                "name": "skill",
                                "input": {"skill": "ship"},
                            }
                        ],
                        text="",
                        tool_calls=[ToolCall(id="skill-1", name="skill", input={"skill": "ship"})],
                    )
                raise ProviderNetworkError("network down")

        session.provider = FailingProvider()

        try:
            with patch.object(
                session,
                "run_forked_skill_mutation",
                return_value=ForkedSkillMutationResult(
                    result_text="forked result",
                    new_messages=[
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": "child prompt"}],
                            "source_kind": "skill_tool_fork",
                            "source_tool_name": "skill",
                            "source_tool_use_id": "skill-1",
                            "skill_name": "ship",
                            "skill_execution_context": "fork",
                        }
                    ],
                    context_update=ToolContextUpdate(
                        allowed_tool_names=("read_file",),
                        source="skill_tool_fork",
                        skill_name="ship",
                    ),
                    injected_message_count=1,
                ),
            ):
                with self.assertRaises(ProviderNetworkError):
                    run_query_loop(session, "hello")

            self.assertEqual(session.state.messages, starting_messages)
            self.assertIsNone(session.transient_tool_context_policy())
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_query_loop_enforces_tool_round_limit(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                max_tool_rounds_per_turn=1,
            )
        )

        class FakeProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                self.calls += 1
                return AssistantResponse(
                    content=[
                        {
                            "type": "tool_use",
                            "id": f"call-{self.calls}",
                            "name": "task_list",
                            "input": {},
                        }
                    ],
                    text="",
                    tool_calls=[ToolCall(id=f"call-{self.calls}", name="task_list", input={})],
                )

        session.provider = FakeProvider()
        with self.assertRaises(RuntimeError):
            run_query_loop(session, "hello")
        self.assertEqual(session.provider.calls, 2)
        self.assertEqual(session.state.messages, [])

    def test_query_loop_emits_streamed_text_chunks(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )
        events: list[RuntimeEvent] = []

        class StreamingProvider:
            def __init__(self) -> None:
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="stream-model",
                    supports_tool_calling=True,
                    supports_streaming=True,
                    supports_structured_output=False,
                )

            def stream_message(self, *, messages, tools, system_prompt):
                yield ProviderStreamEvent(kind="text_delta", text="hel")
                yield ProviderStreamEvent(kind="text_delta", text="lo")
                yield ProviderStreamEvent(
                    kind="response",
                    response=AssistantResponse(
                        content=[{"type": "text", "text": "hello"}],
                        text="hello",
                        tool_calls=[],
                    ),
                )

        session.provider = StreamingProvider()
        result = run_query_loop(session, "hello", sink=events.append)

        self.assertEqual(result, "hello")
        text_events = [event.message for event in events if event.kind == "assistant_text"]
        self.assertEqual(text_events, ["hel", "lo"])

    def test_query_loop_emits_tool_transition_events(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )
        events: list[RuntimeEvent] = []

        class ToolLoopProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="tool-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                self.calls += 1
                if self.calls == 1:
                    return AssistantResponse(
                        content=[
                            {"type": "text", "text": "Looking..."},
                            {"type": "tool_use", "id": "call-1", "name": "task_list", "input": {}},
                        ],
                        text="Looking...",
                        tool_calls=[ToolCall(id="call-1", name="task_list", input={})],
                    )
                return AssistantResponse(
                    content=[{"type": "text", "text": "done"}],
                    text="done",
                    tool_calls=[],
                )

        session.provider = ToolLoopProvider()
        result = run_query_loop(session, "hello", sink=events.append)

        self.assertEqual(result, "done")
        event_kinds = [event.kind for event in events]
        self.assertIn("assistant_tool_call", event_kinds)
        self.assertIn("assistant_tool_result_ready", event_kinds)

    def test_query_loop_emits_provider_usage_runtime_event(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )
        events: list[RuntimeEvent] = []

        class UsageProvider:
            def __init__(self) -> None:
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="usage-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                return AssistantResponse(
                    content=[{"type": "text", "text": "done"}],
                    text="done",
                    tool_calls=[],
                    usage=TokenUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18),
                )

        session.provider = UsageProvider()
        result = run_query_loop(session, "hello", sink=events.append)

        self.assertEqual(result, "done")
        usage_events = [event for event in events if event.kind == "assistant_usage"]
        self.assertEqual(len(usage_events), 1)
        self.assertEqual(usage_events[0].usage_source, "provider")
        self.assertEqual(usage_events[0].total_tokens, 18)
        runtime_budget = session.runtime_budget_state_payload()
        self.assertEqual(runtime_budget["last_turn_token_source"], "provider")
        self.assertEqual(runtime_budget["last_turn_token_count"], 18)
        self.assertTrue(runtime_budget["provider_usage_seen"])

    def test_query_loop_emits_prompt_cache_hints_applied_for_cache_capable_provider(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )
        events: list[RuntimeEvent] = []

        class CacheAwareProvider:
            def __init__(self) -> None:
                self.capabilities = ProviderCapabilities(
                    provider="anthropic",
                    model="cache-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                    supports_prompt_cache_hints=True,
                    supports_system_prompt_cache_blocks=True,
                    supports_tool_schema_cache_hints=True,
                )
                self.cache_plans = []

            def create_message(self, *, messages, tools, system_prompt, cache_plan=None):
                del messages, tools, system_prompt
                self.cache_plans.append(cache_plan)
                return AssistantResponse(
                    content=[{"type": "text", "text": "done"}],
                    text="done",
                    tool_calls=[],
                )

        provider = CacheAwareProvider()
        session.provider = provider

        result = run_query_loop(session, "hello", sink=events.append)

        self.assertEqual(result, "done")
        non_null_cache_plans = [plan for plan in provider.cache_plans if plan is not None]
        self.assertTrue(non_null_cache_plans)
        self.assertEqual(non_null_cache_plans[0].provider_cache_mode, "provider_hinted")
        self.assertIn(
            non_null_cache_plans[0].orchestration_mode,
            {"under_budget", "selected"},
        )
        self.assertTrue(any(event.kind == "prompt_cache_hints_applied" for event in events))
        self.assertTrue(any(event.kind == "prompt_prefix_planner_applied" for event in events))
        self.assertEqual(
            session.prompt_prefix_surface_payload()["prompt_prefix_cache_mode"],
            "provider_hinted",
        )

    def test_query_loop_records_estimated_usage_in_runtime_budget_state(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )

        class UsageFreeProvider:
            def __init__(self) -> None:
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="usage-free-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                return AssistantResponse(
                    content=[{"type": "text", "text": "done"}],
                    text="done",
                    tool_calls=[],
                )

        session.provider = UsageFreeProvider()
        result = run_query_loop(session, "hello")

        self.assertEqual(result, "done")
        runtime_budget = session.runtime_budget_state_payload()
        self.assertEqual(runtime_budget["last_turn_token_source"], "estimated")
        self.assertGreater(int(runtime_budget["last_turn_token_count"] or 0), 0)
        self.assertFalse(runtime_budget["provider_usage_seen"])

    def test_query_loop_recovers_from_prompt_too_long_with_compact_retry(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                max_tokens=20000,
                max_history_messages=10,
                history_keep_last_messages=2,
            )
        )
        session.state.messages = [
            {"role": "user", "content": [{"type": "text", "text": "one"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
            {"role": "user", "content": [{"type": "text", "text": "three"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "four"}]},
        ]
        events: list[RuntimeEvent] = []

        class RecoveryProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="recovery-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                self.calls += 1
                if self.calls == 1:
                    raise ProviderContextLimitError(
                        "maximum context length exceeded for this request"
                    )
                return AssistantResponse(
                    content=[{"type": "text", "text": "done after recovery"}],
                    text="done after recovery",
                    tool_calls=[],
                )

        session.provider = RecoveryProvider()
        result = run_query_loop(session, "hello", sink=events.append)

        self.assertEqual(result, "done after recovery")
        self.assertEqual(session.provider.calls, 2)
        self.assertEqual(session.state.history_boundaries[-1].kind, "compact")
        self.assertEqual(session.state.history_boundaries[-1].trigger, "recovery")
        self.assertIn(
            "prompt-too-long: maximum context length exceeded for this request",
            session.state.history_boundaries[-1].trigger_reason or "",
        )
        self.assertTrue(any(event.kind == "provider_retry" for event in events))
        self.assertTrue(any(event.kind == "context_compacted" for event in events))
        self.assertTrue(any(event.kind == "compact_recovery_started" for event in events))
        self.assertTrue(any(event.kind == "compact_recovery_finished" and not event.is_error for event in events))

    def test_query_loop_recovers_from_prompt_too_long_after_tool_results(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                max_tokens=20000,
                max_history_messages=10,
                history_keep_last_messages=2,
            )
        )
        session.state.messages = [
            {"role": "user", "content": [{"type": "text", "text": "one"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
            {"role": "user", "content": [{"type": "text", "text": "three"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "four"}]},
        ]
        events: list[RuntimeEvent] = []

        class RecoveryProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="recovery-tool-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                self.calls += 1
                if self.calls == 1:
                    return AssistantResponse(
                        content=[
                            {"type": "text", "text": "Looking..."},
                            {"type": "tool_use", "id": "call-1", "name": "task_list", "input": {}},
                        ],
                        text="Looking...",
                        tool_calls=[ToolCall(id="call-1", name="task_list", input={})],
                    )
                if self.calls == 2:
                    raise ProviderContextLimitError("prompt too long after tool results")
                return AssistantResponse(
                    content=[{"type": "text", "text": "done after tool recovery"}],
                    text="done after tool recovery",
                    tool_calls=[],
                )

        session.provider = RecoveryProvider()
        result = run_query_loop(session, "hello", sink=events.append)

        self.assertEqual(result, "done after tool recovery")
        self.assertEqual(session.provider.calls, 3)
        self.assertEqual(session.state.history_boundaries[-1].trigger, "recovery")
        self.assertTrue(any(event.kind == "tool_result_summarized" for event in events))
        self.assertTrue(any(event.kind == "compact_recovery_started" for event in events))
        self.assertTrue(any(event.kind == "compact_recovery_finished" and not event.is_error for event in events))

    def test_query_loop_replaces_large_tool_results_before_provider_call(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                max_tokens=30000,
                max_history_messages=20,
            )
        )
        events: list[RuntimeEvent] = []

        class ReplacementProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.second_call_messages = None
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="replacement-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del tools, system_prompt
                self.calls += 1
                if self.calls == 1:
                    return AssistantResponse(
                        content=[
                            {"type": "text", "text": "Inspecting..."},
                            {"type": "tool_use", "id": "call-1", "name": "task_list", "input": {}},
                        ],
                        text="Inspecting...",
                        tool_calls=[ToolCall(id="call-1", name="task_list", input={})],
                    )
                self.second_call_messages = messages
                return AssistantResponse(
                    content=[{"type": "text", "text": "done with replacement"}],
                    text="done with replacement",
                    tool_calls=[],
                )

        session.provider = ReplacementProvider()
        session.execute_tool_calls = lambda tool_calls, ctx, sink=None: [  # type: ignore[method-assign]
            {
                "type": "tool_result",
                "tool_use_id": "call-1",
                "content": "X" * 20000,
                "is_error": False,
            }
        ]

        result = run_query_loop(session, "hello", sink=events.append)

        self.assertEqual(result, "done with replacement")
        self.assertEqual(session.provider.calls, 2)
        second_messages = session.provider.second_call_messages
        self.assertIsNotNone(second_messages)
        tool_result_block = second_messages[-1]["content"][0]
        self.assertEqual(tool_result_block["tool_use_id"], "call-1")
        self.assertIn("Tool result replaced for context budget.", tool_result_block["content"])
        self.assertEqual(len(session.state.tool_result_replacement_records), 1)
        self.assertEqual(len(session.state.tool_result_artifact_records), 1)
        self.assertEqual(
            session.state.tool_result_replacement_records[0].tool_use_id,
            "call-1",
        )
        self.assertTrue(Path(session.state.tool_result_artifact_records[0].artifact_path).exists())
        self.assertFalse(any(boundary.kind == "compact" for boundary in session.state.history_boundaries))
        self.assertTrue(
            any(event.kind == "tool_result_replacement_applied" for event in events)
        )
        self.assertTrue(any(event.kind == "tool_result_artifact_created" for event in events))

    def test_query_loop_microcompacts_large_tool_results_before_provider_call(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                max_tokens=5000,
                max_history_messages=20,
            )
        )
        events: list[RuntimeEvent] = []

        class MicrocompactProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.second_call_messages = None
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="microcompact-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del tools, system_prompt
                self.calls += 1
                if self.calls == 1:
                    return AssistantResponse(
                        content=[
                            {"type": "text", "text": "Inspecting..."},
                            {"type": "tool_use", "id": "call-1", "name": "task_list", "input": {}},
                        ],
                        text="Inspecting...",
                        tool_calls=[ToolCall(id="call-1", name="task_list", input={})],
                    )
                self.second_call_messages = messages
                return AssistantResponse(
                    content=[{"type": "text", "text": "done with microcompact"}],
                    text="done with microcompact",
                    tool_calls=[],
                )

        session.provider = MicrocompactProvider()
        session.execute_tool_calls = lambda tool_calls, ctx, sink=None: [  # type: ignore[method-assign]
            {
                "type": "tool_result",
                "tool_use_id": "call-1",
                "content": "Y" * 5000,
                "is_error": False,
            }
        ]

        result = run_query_loop(session, "hello", sink=events.append)

        self.assertEqual(result, "done with microcompact")
        self.assertEqual(len(session.state.tool_result_replacement_records), 1)
        self.assertEqual(len(session.state.tool_result_artifact_records), 0)
        self.assertIsNotNone(session.provider.second_call_messages)
        tool_result_block = session.provider.second_call_messages[-1]["content"][0]
        self.assertIn("Tool result replaced for context budget.", tool_result_block["content"])
        self.assertTrue(any(event.kind == "tool_result_microcompacted" for event in events))
        self.assertFalse(any(event.kind == "tool_result_artifact_created" for event in events))

    def test_query_loop_re_raises_prompt_too_long_without_compaction_path(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )

        class NoPathProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="no-path-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                self.calls += 1
                raise ProviderContextLimitError("prompt too long with no compaction path")

        session.provider = NoPathProvider()
        with self.assertRaises(ProviderContextLimitError):
            run_query_loop(session, "hello")
        self.assertEqual(session.provider.calls, 1)
        self.assertEqual(session.state.history_boundaries, [])

    def test_query_loop_fails_when_recovery_budget_remains_over_limit(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                max_tokens=20000,
                max_context_summary_chars=10,
                max_history_messages=6,
                history_keep_last_messages=2,
            )
        )
        session.state.messages = [
            {"role": "user", "content": [{"type": "text", "text": "one"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
            {"role": "user", "content": [{"type": "text", "text": "three"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "four"}]},
        ]

        class RecoveryProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="recovery-fail-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                self.calls += 1
                raise ProviderContextLimitError("maximum context length exceeded")

        session.provider = RecoveryProvider()
        with self.assertRaisesRegex(RuntimeError, "Prompt-too-long recovery failed after compaction"):
            run_query_loop(session, "hello")
        self.assertEqual(session.provider.calls, 1)
        self.assertEqual(session.state.history_boundaries[-1].trigger, "recovery")

    def test_query_loop_emits_budget_pressure_for_warning_state(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                max_tokens=20000,
                max_history_messages=8,
                history_keep_last_messages=2,
            )
        )
        session.state.messages = [
            {"role": "user", "content": [{"type": "text", "text": "one"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
            {"role": "user", "content": [{"type": "text", "text": "three"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "four"}]},
            {"role": "user", "content": [{"type": "text", "text": "five"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "six"}]},
        ]
        events: list[RuntimeEvent] = []

        class FakeProvider:
            def __init__(self) -> None:
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                return AssistantResponse(
                    content=[{"type": "text", "text": "done"}],
                    text="done",
                    tool_calls=[],
                )

        session.provider = FakeProvider()
        result = run_query_loop(session, "hello", sink=events.append)

        self.assertEqual(result, "done")
        budget_events = [event for event in events if event.kind == "budget_pressure"]
        self.assertGreaterEqual(len(budget_events), 1)
        self.assertTrue(all(event.budget_state == "warning" for event in budget_events))
        self.assertTrue(
            all("warning threshold" in (event.budget_reason or "") for event in budget_events)
        )

    def test_query_loop_does_not_recover_twice_from_prompt_too_long(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                max_tokens=20000,
                max_history_messages=6,
                history_keep_last_messages=2,
            )
        )
        session.state.messages = [
            {"role": "user", "content": [{"type": "text", "text": "one"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
            {"role": "user", "content": [{"type": "text", "text": "three"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "four"}]},
        ]

        class RepeatProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="repeat-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                self.calls += 1
                raise ProviderContextLimitError("prompt too long again")

        session.provider = RepeatProvider()
        with self.assertRaises(ProviderContextLimitError):
            run_query_loop(session, "hello")
        self.assertEqual(session.provider.calls, 2)

    def test_query_loop_hard_stops_when_budget_has_no_compaction_path(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                max_context_summary_chars=10,
            )
        )
        session.state.context_summary = "x" * 20

        class FakeProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                self.calls += 1
                return AssistantResponse(
                    content=[{"type": "text", "text": "should not happen"}],
                    text="should not happen",
                    tool_calls=[],
                )

        session.provider = FakeProvider()
        with self.assertRaisesRegex(
            RuntimeError,
            "Message budget exceeded without a recoverable compaction path",
        ):
            run_query_loop(session, "hello")
        self.assertEqual(session.provider.calls, 0)

    def test_background_task_sink_tracks_runtime_progress_metadata(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )
        session.set_background_session_link("bg-123")
        task = session.task_manager.create(
            "agent",
            "Finish background work",
            **session._task_background_metadata(),
            task_role="background",
        )
        sink = session._build_background_task_sink(task.id)

        sink(
            RuntimeEvent(
                kind="assistant_usage",
                message="estimated response usage",
                total_tokens=12,
                usage_source="estimated",
            )
        )
        sink(
            RuntimeEvent(
                kind="tool_started",
                message='{"path":"demo.py"}',
                tool_name="read_file",
                tool_call_id="call-1",
            )
        )
        sink(
            RuntimeEvent(
                kind="tool_waiting_for_approval",
                message='{"path":"demo.py"}',
                tool_name="read_file",
                tool_call_id="call-1",
                approval_risk_level="read",
            )
        )
        sink(
            RuntimeEvent(
                kind="tool_finished",
                message="ok",
                tool_name="read_file",
                tool_call_id="call-1",
                duration_ms=25,
            )
        )
        sink(
            RuntimeEvent(
                kind="tool_result_summarized",
                message="ok results=1",
                result_count=1,
            )
        )
        sink(
            RuntimeEvent(
                kind="budget_pressure",
                message="message count 6 >= warning threshold 6",
                budget_state="warning",
                budget_reason="message count 6 >= warning threshold 6",
            )
        )
        sink(
            RuntimeEvent(
                kind="compact_recovery_started",
                message="starting compact recovery after prompt-too-long",
                compaction_trigger="recovery",
                budget_state="compact_needed",
                budget_reason="prompt too long",
                is_error=True,
            )
        )
        sink(
            RuntimeEvent(
                kind="task_progress",
                message="Reviewing runtime metadata",
                task_id=task.id,
            )
        )

        updated = session.task_manager.get(task.id)
        assert updated is not None
        self.assertEqual(updated.metadata["background_token_count"], 15)
        self.assertEqual(updated.metadata["background_token_count_source"], "estimated")
        self.assertEqual(updated.metadata["background_last_tool"], "read_file")
        self.assertEqual(updated.metadata["background_last_tool_input"], '{"path":"demo.py"}')
        self.assertEqual(updated.metadata["background_last_tool_summary"], "ok (25ms)")
        self.assertEqual(updated.metadata["background_runtime_active_tool_status"], "none")
        self.assertFalse(updated.metadata["background_runtime_parallel_batch_active"])
        self.assertEqual(updated.metadata["background_runtime_parallel_batch_size"], 0)
        self.assertEqual(updated.metadata["background_runtime_last_result_summary"], "ok results=1")
        self.assertEqual(updated.metadata["background_runtime_budget_pressure_summary"], "message count 6 >= warning threshold 6")
        self.assertEqual(updated.metadata["background_runtime_compact_recovery_summary"], "starting compact recovery after prompt-too-long")
        self.assertEqual(updated.metadata["background_runtime_last_tool_result_summary"], "ok results=1")
        self.assertEqual(updated.metadata["background_runtime_last_budget_pressure"], "message count 6 >= warning threshold 6")
        self.assertEqual(updated.metadata["background_runtime_last_compact_recovery"], "starting compact recovery after prompt-too-long")
        self.assertEqual(updated.metadata["background_recent_activity_kind"], "compact_recovery")
        self.assertEqual(
            updated.metadata["background_recent_activity"],
            "starting compact recovery after prompt-too-long",
        )
        self.assertEqual(
            updated.metadata["background_progress_summary"],
            "starting compact recovery after prompt-too-long",
        )

    def test_runtime_progress_surface_tracks_tool_lifecycle(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )
        try:
            emit = session.build_runtime_event_sink(None)
            emit(
                RuntimeEvent(
                    kind="tool_batch_started",
                    message="starting 2 parallel read-only tool call(s)",
                    batch_size=2,
                    batch_parallel=True,
                )
            )
            emit(
                RuntimeEvent(
                    kind="tool_waiting_for_approval",
                    message='{"path":"demo.py"}',
                    tool_name="read_file",
                    tool_call_id="call-1",
                    approval_risk_level="read",
                )
            )
            emit(
                RuntimeEvent(
                    kind="tool_started",
                    message='{"path":"demo.py"}',
                    tool_name="read_file",
                    tool_call_id="call-1",
                )
            )
            emit(
                RuntimeEvent(
                    kind="tool_result_summarized",
                    message="ok results=2",
                    result_count=2,
                )
            )
            emit(
                RuntimeEvent(
                    kind="budget_pressure",
                    message="message count 6 >= warning threshold 6",
                    budget_state="warning",
                    budget_reason="message count 6 >= warning threshold 6",
                )
            )
            emit(
                RuntimeEvent(
                    kind="compact_recovery_started",
                    message="starting compact recovery after prompt-too-long",
                    compaction_trigger="recovery",
                    budget_state="compact_needed",
                    budget_reason="prompt too long",
                )
            )
            emit(
                RuntimeEvent(
                    kind="tool_finished",
                    message="ok",
                    tool_name="read_file",
                    tool_call_id="call-1",
                    duration_ms=25,
                )
            )
            emit(
                RuntimeEvent(
                    kind="tool_batch_finished",
                    message="completed 2 parallel read-only tool call(s)",
                    batch_size=2,
                    batch_parallel=True,
                    result_count=2,
                )
            )

            payload = session.runtime_progress_surface_payload()
            self.assertEqual(payload["runtime_active_tool_status"], "none")
            self.assertEqual(payload["runtime_last_tool_name"], "read_file")
            self.assertEqual(payload["runtime_last_tool_status"], "ok")
            self.assertEqual(payload["runtime_last_tool_summary"], "ok (25ms)")
            self.assertEqual(payload["runtime_last_result_summary"], "ok results=2")
            self.assertEqual(
                payload["runtime_budget_pressure_summary"],
                "message count 6 >= warning threshold 6",
            )
            self.assertEqual(
                payload["runtime_compact_recovery_summary"],
                "starting compact recovery after prompt-too-long",
            )
            self.assertFalse(payload["runtime_parallel_batch_active"])
            self.assertEqual(payload["runtime_parallel_batch_size"], 0)
        finally:
            session.close()

    def test_query_loop_applies_advisor_revision_to_final_answer(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )
        session.state.advisor_model = "advisor-model"
        events: list[RuntimeEvent] = []

        class MainProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="main-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                self.calls += 1
                text = "draft answer" if self.calls == 1 else "revised answer"
                return AssistantResponse(
                    content=[{"type": "text", "text": text}],
                    text=text,
                    tool_calls=[],
                )

        class AdvisorProvider:
            def __init__(self) -> None:
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="advisor-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                return AssistantResponse(
                    content=[{"type": "text", "text": "REVISE: tighten the answer"}],
                    text="REVISE: tighten the answer",
                    tool_calls=[],
                )

        session.provider = MainProvider()
        with patch.object(session, "build_advisor_provider", return_value=AdvisorProvider()):
            result = run_query_loop(session, "hello", sink=events.append)

        self.assertEqual(result, "revised answer")
        self.assertEqual(session.provider.calls, 2)
        self.assertEqual(session.state.messages[-1]["content"][0]["text"], "revised answer")
        self.assertTrue(any(event.kind == "advisor" for event in events))
        self.assertTrue(any("applied revised final answer" in event.message for event in events))

    def test_query_loop_keeps_draft_when_advisor_review_fails(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )
        session.state.advisor_model = "advisor-model"
        events: list[RuntimeEvent] = []

        class MainProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="main-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                self.calls += 1
                return AssistantResponse(
                    content=[{"type": "text", "text": "draft answer"}],
                    text="draft answer",
                    tool_calls=[],
                )

        class FailingAdvisorProvider:
            def __init__(self) -> None:
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="advisor-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                raise ProviderNetworkError("advisor down")

        session.provider = MainProvider()
        with patch.object(session, "build_advisor_provider", return_value=FailingAdvisorProvider()):
            result = run_query_loop(session, "hello", sink=events.append)

        self.assertEqual(result, "draft answer")
        self.assertEqual(session.provider.calls, 1)
        self.assertEqual(session.state.messages[-1]["content"][0]["text"], "draft answer")
        self.assertTrue(any(event.kind == "advisor" and event.is_error for event in events))

    def test_query_loop_interactive_advisor_revises_initial_plan(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )
        session.state.advisor_model = "advisor-model"
        session.state.advisor_mode = "interactive-review"
        events: list[RuntimeEvent] = []

        class MainProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="main-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                self.calls += 1
                text = "initial plan" if self.calls == 1 else "revised plan"
                return AssistantResponse(
                    content=[{"type": "text", "text": text}],
                    text=text,
                    tool_calls=[],
                )

        class AdvisorProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.plan_drift_prompt = ""
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="advisor-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                self.calls += 1
                payload = (
                    {
                        "status": "revise",
                        "reason": "Need a clearer plan",
                        "suggested_changes": ["Add concrete execution steps"],
                        "risk_flags": ["underspecified"],
                    }
                    if self.calls == 1
                    else {
                        "status": "approve",
                        "reason": "Looks good",
                        "suggested_changes": [],
                        "risk_flags": [],
                    }
                )
                text = json.dumps(payload)
                return AssistantResponse(
                    content=[{"type": "text", "text": text}],
                    text=text,
                    tool_calls=[],
                )

        session.provider = MainProvider()
        advisor_provider = AdvisorProvider()
        with patch.object(session, "build_advisor_provider", return_value=advisor_provider):
            result = run_query_loop(session, "plan the refactor", sink=events.append)

        self.assertEqual(result, "revised plan")
        self.assertEqual(session.provider.calls, 2)
        self.assertEqual(session.state.advisor_review_history[0].checkpoint, "initial_plan")
        self.assertTrue(
            any(
                event.kind == "advisor_revision_requested"
                and "checkpoint=initial_plan" in event.message
                for event in events
            )
        )

    def test_query_loop_interactive_advisor_blocks_before_write_until_revised(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )
        session.state.advisor_model = "advisor-model"
        session.state.advisor_mode = "interactive-review"
        events: list[RuntimeEvent] = []

        class MainProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="main-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                self.calls += 1
                if self.calls == 1:
                    return AssistantResponse(
                        content=[
                            {"type": "text", "text": "I will patch the file"},
                            {"type": "tool_use", "id": "call-1", "name": "write_file", "input": {"path": "demo.py"}},
                        ],
                        text="I will patch the file",
                        tool_calls=[ToolCall(id="call-1", name="write_file", input={"path": "demo.py"})],
                    )
                return AssistantResponse(
                    content=[{"type": "text", "text": "safe final answer"}],
                    text="safe final answer",
                    tool_calls=[],
                )

        class AdvisorProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.plan_drift_prompt = ""
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="advisor-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del tools, system_prompt
                self.calls += 1
                if self.calls == 2:
                    self.plan_drift_prompt = messages[0]["content"][0]["text"]
                payload = (
                    {
                        "status": "approve",
                        "reason": "Initial plan is acceptable",
                        "suggested_changes": [],
                        "risk_flags": [],
                    }
                    if self.calls == 1
                    else {
                        "status": "block",
                        "reason": "Do not write before a safer approach is explicit",
                        "suggested_changes": ["Re-explain the safe path first"],
                        "risk_flags": ["unsafe-write"],
                    }
                    if self.calls == 2
                    else {
                        "status": "approve",
                        "reason": "Final answer is acceptable",
                        "suggested_changes": [],
                        "risk_flags": [],
                    }
                )
                text = json.dumps(payload)
                return AssistantResponse(
                    content=[{"type": "text", "text": text}],
                    text=text,
                    tool_calls=[],
                )

        session.provider = MainProvider()
        advisor_provider = AdvisorProvider()
        with patch.object(session, "build_advisor_provider", return_value=advisor_provider):
            with patch.object(session, "execute_tool_calls", side_effect=AssertionError("should not execute write tools")):
                result = run_query_loop(session, "fix the file", sink=events.append)

        self.assertEqual(result, "safe final answer")
        self.assertTrue(
            any(
                event.kind == "advisor_revision_requested"
                and "checkpoint=before_write" in event.message
                for event in events
            )
        )
        self.assertEqual(session.state.advisor_review_history[1].checkpoint, "before_write")
        self.assertEqual(session.state.advisor_review_history[1].status, "block")

    def test_query_loop_explicitly_reuses_active_ultraplan_artifact(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="refactor session runtime",
                summary="Touch session.py and runtime/context.py in that order.",
                used_read_only_subagents=True,
                scout_categories=["architecture-boundaries"],
                task_ids=["task-1"],
                advisor_status="approve",
            )
        )

        class FakeProvider:
            def __init__(self) -> None:
                self.seen_prompt = ""
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del tools, system_prompt
                self.seen_prompt = messages[-1]["content"][0]["text"]
                return AssistantResponse(
                    content=[{"type": "text", "text": "done"}],
                    text="done",
                    tool_calls=[],
                )

        session.provider = FakeProvider()
        result = run_query_loop(session, "implement the refactor")

        self.assertEqual(result, "done")
        self.assertIn("Use the recent ultraplan artifact below as explicit execution context", session.provider.seen_prompt)
        self.assertIn("Plan goal: refactor session runtime", session.provider.seen_prompt)
        self.assertIn("Current user request:\nimplement the refactor", session.provider.seen_prompt)

    def test_query_loop_emits_plan_execution_event_for_active_plan(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="stabilize query loop",
                summary="Start from runtime/query_loop.py, then update session.py if needed.",
                used_read_only_subagents=True,
            )
        )
        events: list[RuntimeEvent] = []

        class FakeProvider:
            def __init__(self) -> None:
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                return AssistantResponse(
                    content=[{"type": "text", "text": "done"}],
                    text="done",
                    tool_calls=[],
                )

        session.provider = FakeProvider()
        result = run_query_loop(session, "implement it", sink=events.append)

        self.assertEqual(result, "done")
        self.assertTrue(any(event.kind == "plan_execution" for event in events))
        self.assertEqual(session.state.plan_execution_count, 1)
        self.assertIsNone(session.state.active_execution_plan_id)
        self.assertEqual(len(session.task_manager.list()), 1)
        task = session.task_manager.list()[0]
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.kind, "plan_execution")
        self.assertEqual(task.metadata["task_role"], "execution")
        self.assertEqual(task.metadata["plan_execution_mode"], "interactive_turn")
        self.assertEqual(task.metadata["plan_execution_phase"], "completed")
        self.assertEqual(task.metadata["plan_status"], "on-plan")
        self.assertEqual(task.metadata["active_plan_id"], session.planning_artifacts()[-1].artifact_id)

    def test_query_loop_does_not_reuse_plan_after_clear(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="refactor session runtime",
                summary="Touch session.py and runtime/context.py in that order.",
                used_read_only_subagents=True,
            )
        )
        session.clear_active_plan()

        class FakeProvider:
            def __init__(self) -> None:
                self.seen_prompt = ""
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del tools, system_prompt
                self.seen_prompt = messages[-1]["content"][0]["text"]
                return AssistantResponse(
                    content=[{"type": "text", "text": "done"}],
                    text="done",
                    tool_calls=[],
                )

        session.provider = FakeProvider()
        result = run_query_loop(session, "implement the refactor")

        self.assertEqual(result, "done")
        self.assertEqual(session.provider.seen_prompt, "implement the refactor")

    def test_query_loop_without_active_plan_does_not_create_execution_task(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )

        class FakeProvider:
            def __init__(self) -> None:
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="fake-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, tools, system_prompt
                return AssistantResponse(
                    content=[{"type": "text", "text": "done"}],
                    text="done",
                    tool_calls=[],
                )

        session.provider = FakeProvider()
        result = run_query_loop(session, "plain ask")

        self.assertEqual(result, "done")
        self.assertEqual(session.task_manager.list(), [])

    def test_query_loop_enforces_read_only_toolset_after_before_write_revision(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )
        session.state.advisor_model = "advisor-model"
        session.state.advisor_mode = "interactive-review"

        class MainProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.second_call_tools: list[str] = []
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="main-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del messages, system_prompt
                self.calls += 1
                if self.calls == 1:
                    return AssistantResponse(
                        content=[
                            {"type": "text", "text": "I will patch the file"},
                            {"type": "tool_use", "id": "call-1", "name": "write_file", "input": {"path": "demo.py"}},
                        ],
                        text="I will patch the file",
                        tool_calls=[ToolCall(id="call-1", name="write_file", input={"path": "demo.py"})],
                    )
                self.second_call_tools = [str(item.get("name")) for item in tools]
                return AssistantResponse(
                    content=[{"type": "text", "text": "safe final answer"}],
                    text="safe final answer",
                    tool_calls=[],
                )

        class AdvisorProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="advisor-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del tools, system_prompt
                self.calls += 1
                if self.calls == 2:
                    self.plan_drift_prompt = messages[0]["content"][0]["text"]
                payload = (
                    {
                        "status": "approve",
                        "reason": "Initial plan is acceptable",
                        "suggested_changes": [],
                        "risk_flags": [],
                    }
                    if self.calls == 1
                    else {
                        "status": "revise",
                        "reason": "Stay read-only until the safer path is explicit",
                        "suggested_changes": ["Investigate first"],
                        "risk_flags": ["unsafe-write"],
                    }
                    if self.calls == 2
                    else {
                        "status": "approve",
                        "reason": "Final answer is acceptable",
                        "suggested_changes": [],
                        "risk_flags": [],
                    }
                )
                text = json.dumps(payload)
                return AssistantResponse(
                    content=[{"type": "text", "text": text}],
                    text=text,
                    tool_calls=[],
                )

        session.provider = MainProvider()
        advisor_provider = AdvisorProvider()
        with patch.object(session, "build_advisor_provider", return_value=advisor_provider):
            result = run_query_loop(session, "fix the file")

        self.assertEqual(result, "safe final answer")
        self.assertNotIn("write_file", session.provider.second_call_tools)
        self.assertNotIn("edit_file", session.provider.second_call_tools)
        self.assertNotIn("apply_patch", session.provider.second_call_tools)
        self.assertEqual(session.state.active_execution_constraint, "normal")
        self.assertIsNone(session.state.constraint_source)
        self.assertIsNone(session.state.constraint_reason)
        self.assertEqual(session.state.constraint_trigger_count, 1)

    def test_query_loop_plan_drift_forces_read_only_revision_before_continuing(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
            )
        )
        session.state.advisor_model = "advisor-model"
        session.state.advisor_mode = "interactive-review"
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="stabilize runtime context plumbing",
                summary="Keep changes focused on runtime/query_loop.py and session.py.",
                used_read_only_subagents=True,
                scout_categories=["architecture-boundaries"],
                task_ids=["task-1"],
                advisor_status="approve",
            )
        )
        events: list[RuntimeEvent] = []
        case = self

        class MainProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.second_call_tools: list[str] = []
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="main-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del system_prompt
                self.calls += 1
                if self.calls == 1:
                    return AssistantResponse(
                        content=[
                            {"type": "text", "text": "I will rewrite cli.py right away."},
                            {"type": "tool_use", "id": "call-1", "name": "write_file", "input": {"path": "cli.py"}},
                        ],
                        text="I will rewrite cli.py right away.",
                        tool_calls=[ToolCall(id="call-1", name="write_file", input={"path": "cli.py"})],
                )
                self.second_call_tools = [str(item.get("name")) for item in tools]
                case.assertIn("Active plan to align with:", messages[-1]["content"][0]["text"])
                return AssistantResponse(
                    content=[{"type": "text", "text": "I will stay within runtime/query_loop.py and session.py."}],
                    text="I will stay within runtime/query_loop.py and session.py.",
                    tool_calls=[],
                )

        class AdvisorProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.plan_drift_prompt = ""
                self.capabilities = ProviderCapabilities(
                    provider="fake",
                    model="advisor-model",
                    supports_tool_calling=True,
                    supports_streaming=False,
                    supports_structured_output=False,
                )

            def create_message(self, *, messages, tools, system_prompt):
                del tools, system_prompt
                self.calls += 1
                if self.calls == 2:
                    self.plan_drift_prompt = messages[0]["content"][0]["text"]
                payload = (
                    {
                        "status": "approve",
                        "reason": "Initial plan is acceptable",
                        "suggested_changes": [],
                        "risk_flags": [],
                    }
                    if self.calls == 1
                    else {
                        "status": "block",
                        "reason": "This drifts away from the active plan scope.",
                        "suggested_changes": ["Stay within the runtime/query_loop.py and session.py work."],
                        "risk_flags": ["plan-drift"],
                    }
                    if self.calls == 2
                    else {
                        "status": "approve",
                        "reason": "Revised work is aligned with the plan.",
                        "suggested_changes": [],
                        "risk_flags": [],
                    }
                )
                text = json.dumps(payload)
                return AssistantResponse(
                    content=[{"type": "text", "text": text}],
                    text=text,
                    tool_calls=[],
                )

        session.provider = MainProvider()
        advisor_provider = AdvisorProvider()
        with patch.object(session, "build_advisor_provider", return_value=advisor_provider):
            result = run_query_loop(session, "implement the active plan", sink=events.append)

        self.assertEqual(result, "I will stay within runtime/query_loop.py and session.py.")
        self.assertNotIn("write_file", session.provider.second_call_tools)
        self.assertNotIn("edit_file", session.provider.second_call_tools)
        self.assertNotIn("apply_patch", session.provider.second_call_tools)
        self.assertTrue(
            any(
                event.kind == "advisor_revision_requested"
                and "checkpoint=plan_drift" in event.message
                for event in events
            )
        )
        self.assertIn("Plan drift analysis:", advisor_provider.plan_drift_prompt)
        self.assertIn("active_plan_vs_candidate_diff:", advisor_provider.plan_drift_prompt)
        self.assertIn("pending_tools: write_file", advisor_provider.plan_drift_prompt)
        self.assertEqual(session.state.plan_execution_count, 1)
        self.assertEqual(session.state.plan_drift_count, 1)
        self.assertEqual(session.state.last_plan_drift_status, "block")
        self.assertEqual(session.state.last_plan_drift_reason, "This drifts away from the active plan scope.")
        self.assertEqual(session.state.active_execution_constraint, "normal")
        self.assertIsNone(session.state.constraint_source)
        self.assertIsNone(session.state.constraint_reason)
        self.assertEqual(session.state.constraint_trigger_count, 1)
        self.assertEqual(len(session.task_manager.list()), 1)
        task = session.task_manager.list()[0]
        self.assertEqual(task.metadata["plan_execution_mode"], "interactive_turn")
        self.assertEqual(task.metadata["plan_execution_phase"], "completed")
        self.assertEqual(task.metadata["plan_status"], "drifted")
        self.assertEqual(task.metadata["drift_status"], "block")


if __name__ == "__main__":
    unittest.main()
