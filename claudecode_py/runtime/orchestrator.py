from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from ..models import ToolCall
from ..permission_display import PermissionDisplayContext, render_permission_display_compact
from ..permissions import PermissionDeniedError
from ..tools.base import BaseTool, ToolContext, format_tool_output
from .events import EventSink, RuntimeEvent, null_sink, summarize_tool_input


@dataclass(slots=True)
class ToolExecutionResult:
    tool_call_id: str
    content: str
    is_error: bool = False


class ToolOrchestrator:
    def __init__(self, tools: list[BaseTool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def tool_specs(self) -> list[dict[str, Any]]:
        return [tool.to_model_tool() for tool in self._tools.values()]

    def execute_tool_calls(
        self,
        tool_calls: list[ToolCall],
        ctx: ToolContext,
        *,
        sink: EventSink | None = None,
    ) -> list[dict[str, Any]]:
        event_sink = sink or null_sink
        results: list[ToolExecutionResult] = []
        for batch in self._partition(tool_calls):
            batch_parallel = self._is_parallel_batch(batch)
            event_sink(
                RuntimeEvent(
                    kind="tool_batch_started",
                    message=self._batch_message(batch, parallel=batch_parallel, completed=False),
                    batch_size=len(batch),
                    batch_parallel=batch_parallel,
                )
            )
            if batch_parallel:
                with ThreadPoolExecutor(max_workers=min(len(batch), 4)) as executor:
                    futures = [executor.submit(self._run_one, call, ctx, event_sink) for call in batch]
                    results.extend(future.result() for future in futures)
            else:
                for call in batch:
                    results.append(self._run_one(call, ctx, event_sink))
            event_sink(
                RuntimeEvent(
                    kind="tool_batch_finished",
                    message=self._batch_message(batch, parallel=batch_parallel, completed=True),
                    batch_size=len(batch),
                    batch_parallel=batch_parallel,
                    result_count=len(batch),
                )
            )
        return [
            {
                "type": "tool_result",
                "tool_use_id": result.tool_call_id,
                "is_error": result.is_error,
                "content": result.content,
            }
            for result in results
        ]

    def _run_one(
        self,
        call: ToolCall,
        ctx: ToolContext,
        sink: EventSink,
    ) -> ToolExecutionResult:
        tool = self._tools.get(call.name)
        if tool is None:
            sink(
                RuntimeEvent(
                    kind="tool_failed",
                    message=f'Unknown tool "{call.name}".',
                    tool_name=call.name,
                    tool_call_id=call.id,
                    is_error=True,
                )
            )
            return ToolExecutionResult(call.id, f'Unknown tool "{call.name}".', True)

        request = tool.approval_request(call.input, ctx)
        sink(
            RuntimeEvent(
                kind="tool_waiting_for_approval",
                message=summarize_tool_input(call.input),
                tool_name=tool.name,
                tool_call_id=call.id,
                approval_risk_level=request.risk_level,
            )
        )
        started = perf_counter()
        try:
            validator = getattr(ctx.session, "validate_tool_call_policy", None)
            if validator is not None:
                validator(tool.name, call.input)
            ctx.permission_manager.require_approval(request)
            sink(
                RuntimeEvent(
                    kind="tool_started",
                    message=summarize_tool_input(call.input),
                    tool_name=tool.name,
                    tool_call_id=call.id,
                )
            )
            result = tool.execute(call.input, ctx)
            duration_ms = int((perf_counter() - started) * 1000)
            sink(
                RuntimeEvent(
                    kind="tool_finished",
                    message="ok",
                    tool_name=tool.name,
                    tool_call_id=call.id,
                    duration_ms=duration_ms,
                )
            )
            return ToolExecutionResult(call.id, format_tool_output(result))
        except PermissionDeniedError as exc:
            duration_ms = int((perf_counter() - started) * 1000)
            permission_context = PermissionDisplayContext(
                decision_reason=request.decision_reason or "",
                permission_rules=request.permission_rules,
                command_mode_name=request.command_mode_name or "",
                command_mode_source=request.command_mode_source or "",
                command_mode_allowed_prefixes=request.command_mode_allowed_prefixes,
                command_mode_violating_segment=request.command_mode_violating_segment or "",
                command_mode_violating_segment_index=request.command_mode_violating_segment_index,
                command_mode_complex_features=request.command_mode_complex_features,
            )
            result_text = str(exc)
            detail = render_permission_display_compact(permission_context)
            if detail:
                result_text += f" [{detail}]"
            event_kwargs: dict[str, Any] = {
                "decision_reason": request.decision_reason or None,
                "permission_rules": request.permission_rules,
                "command_mode_name": request.command_mode_name or None,
                "command_mode_allowed_prefixes": request.command_mode_allowed_prefixes,
                "command_mode_violating_segment": (
                    request.command_mode_violating_segment or None
                ),
                "command_mode_violating_segment_index": request.command_mode_violating_segment_index,
                "command_mode_complex_features": request.command_mode_complex_features,
            }
            sink(
                RuntimeEvent(
                    kind="tool_failed",
                    message=str(exc),
                    tool_name=tool.name,
                    tool_call_id=call.id,
                    duration_ms=duration_ms,
                    approval_risk_level=request.risk_level,
                    is_error=True,
                    **event_kwargs,
                )
            )
            return ToolExecutionResult(call.id, result_text, True)
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((perf_counter() - started) * 1000)
            error_text = f"{type(exc).__name__}: {exc}"
            sink(
                RuntimeEvent(
                    kind="tool_failed",
                    message=error_text,
                    tool_name=tool.name,
                    tool_call_id=call.id,
                    duration_ms=duration_ms,
                    is_error=True,
                )
            )
            return ToolExecutionResult(call.id, error_text, True)

    def _batch_message(self, batch: list[ToolCall], *, parallel: bool, completed: bool) -> str:
        batch_size = len(batch)
        mode = "parallel read-only" if parallel else "serial"
        phase = "completed" if completed else "starting"
        return f"{phase} {batch_size} {mode} tool call(s)"

    def _partition(self, tool_calls: list[ToolCall]) -> list[list[ToolCall]]:
        batches: list[list[ToolCall]] = []
        for call in tool_calls:
            tool = self._tools.get(call.name)
            is_parallel = bool(tool and tool.read_only and tool.concurrency_safe)
            if is_parallel and batches and self._is_parallel_batch(batches[-1]):
                batches[-1].append(call)
            else:
                batches.append([call])
        return batches

    def _is_parallel_batch(self, batch: list[ToolCall]) -> bool:
        if not batch:
            return False
        tool = self._tools.get(batch[0].name)
        return bool(tool and tool.read_only and tool.concurrency_safe)
