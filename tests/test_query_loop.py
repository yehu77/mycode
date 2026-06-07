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
