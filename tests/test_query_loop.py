from pathlib import Path
import json
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.models import AssistantResponse, ProviderStreamEvent, ToolCall
from claudecode_py.providers.capabilities import ProviderCapabilities
from claudecode_py.providers.errors import (
    ProviderCapabilityError,
    ProviderNetworkError,
    ProviderRateLimitError,
)
from claudecode_py.runtime.events import RuntimeEvent
from claudecode_py.runtime.query_loop import run_query_loop
from claudecode_py.session import Session
from claudecode_py.state import PlanningArtifact


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


if __name__ == "__main__":
    unittest.main()
