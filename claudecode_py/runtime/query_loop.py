from __future__ import annotations

from contextlib import nullcontext
import json
from pathlib import Path
from time import sleep

from ..models import Message
from ..permissions import PermissionDeniedError
from ..providers.errors import (
    ProviderCapabilityError,
    ProviderContextLimitError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from .events import EventSink, RuntimeEvent, summarize_tool_input
from .orchestrator import normalize_tool_batch_execution_result
from .reduction_orchestration import (
    ProviderViewReductionOrchestrationResult,
    build_provider_view_reduction_orchestration,
)
from .tool_result_replacement import (
    ToolResultBudgetResult,
    apply_tool_result_budget_to_messages,
    estimate_provider_call_context_usage,
)


def run_query_loop(
    session: "Session",
    prompt: str,
    *,
    sink: EventSink | None = None,
) -> str:
    from ..session import Session  # local import to avoid cycle

    if not isinstance(session, Session):
        raise TypeError("session must be a Session")

    sink = session.build_runtime_event_sink(sink)
    session.validate_provider_capabilities()
    start_message_count = len(session.state.messages)
    start_context_summary = session.state.context_summary
    start_plan_execution_count = session.state.plan_execution_count
    start_plan_drift_count = session.state.plan_drift_count
    start_last_plan_drift_status = session.state.last_plan_drift_status
    start_last_plan_drift_reason = session.state.last_plan_drift_reason
    start_last_plan_drift_context = session.state.last_plan_drift_context
    start_active_execution_constraint = session.state.active_execution_constraint
    start_constraint_source = session.state.constraint_source
    start_constraint_reason = session.state.constraint_reason
    start_constraint_trigger_count = session.state.constraint_trigger_count
    start_tool_result_replacement_records = list(session.state.tool_result_replacement_records)
    start_tool_result_artifact_records = list(session.state.tool_result_artifact_records)
    start_transient_tool_context_policy = session.transient_tool_context_policy()
    start_transient_runtime_override = session.transient_runtime_override()
    start_session_runtime_mode = session.state.session_runtime_mode
    start_pre_plan_mode = session.state.pre_plan_mode
    start_has_exited_plan_mode = session.state.has_exited_plan_mode
    start_needs_plan_mode_exit_attachment = session.state.needs_plan_mode_exit_attachment
    start_needs_plan_mode_reentry_attachment = session.state.needs_plan_mode_reentry_attachment
    start_plan_mode_attachment_count = session.state.plan_mode_attachment_count
    start_plan_mode_exit_approved_plan = session.state.plan_mode_exit_approved_plan
    start_plan_mode_exit_restored_mode = session.state.plan_mode_exit_restored_mode
    start_plan_slug = session.state.plan_slug
    execution_task_id: str | None = None
    plan_drifted = False
    approved_plan_mode_exit_state: dict[str, object] | None = None

    try:
        session.clear_transient_tool_context_overlay()
        session.clear_transient_runtime_override()
        session.clear_execution_constraint()
        session.state.messages.append(
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            }
        )
        _enforce_message_budget(session, sink)

        final_text = ""
        tool_rounds = 0
        initial_plan_reviewed = False
        write_constraints_active = False
        active_plan = session.active_planning_artifact()
        if active_plan is not None:
            session.begin_plan_execution(active_plan)
            execution_task_id = session.start_active_plan_execution_task(
                prompt=prompt,
                artifact=active_plan,
            )
            session.update_execution_task(
                execution_task_id,
                "Reviewing user request under active plan",
                plan_execution_phase="planning",
                plan_status="on-plan",
            )
            sink(
                RuntimeEvent(
                    kind="plan_execution",
                    message=(
                        f"following artifact={active_plan.artifact_id} "
                        f"kind={active_plan.kind} goal={active_plan.goal}"
                    ),
                )
            )
        planning_prompt = session.build_turn_prompt(prompt)
        planning_prompt_pending = True
        for _ in range(session.config.max_turns):
            with _turn_execution_scope(session, write_constraints_active=write_constraints_active):
                response, streamed_text = _create_message_with_compact_recovery(
                    session,
                    sink,
                    turn_user_prompt=planning_prompt if planning_prompt_pending else None,
                )
            planning_prompt_pending = False
            assistant_message: Message = {"role": "assistant", "content": response.content}
            session.state.messages.append(assistant_message)
            _enforce_message_budget(session, sink)
            _emit_response_usage_event(session, response, sink)

            if response.text:
                final_text = response.text

            pending_tool_names = tuple(tool_call.name for tool_call in response.tool_calls)
            if session.uses_interactive_advisor() and not initial_plan_reviewed:
                initial_plan_reviewed = True
                review = _request_advisor_review(
                    session,
                    checkpoint="initial_plan",
                    user_prompt=prompt,
                    candidate_text=_candidate_text_for_checkpoint(
                        response.text,
                        pending_tool_names=pending_tool_names,
                        fallback_label="Proposed initial plan",
                    ),
                    pending_tool_names=pending_tool_names,
                    sink=sink,
                )
                if _should_request_main_model_revision(review):
                    write_constraints_active = True
                    if execution_task_id is not None:
                        session.update_execution_task(
                            execution_task_id,
                            "Revising initial plan after advisor review",
                            plan_execution_phase="revising",
                            plan_status="on-plan",
                            constraint_source=f"initial_plan_{review.status}",
                        )
                    session.activate_execution_constraint(
                        mode="read-only",
                        source=f"initial_plan_{review.status}",
                        reason=review.reason or "Advisor requested a safer, read-only revision of the initial plan.",
                        increment=True,
                    )
                    session.state.messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": session.build_advisor_followup_prompt(
                                        checkpoint="initial_plan",
                                        advisor_review=review,
                                        pending_tool_names=pending_tool_names,
                                    ),
                                }
                            ],
                        }
                    )
                    _enforce_message_budget(session, sink)
                    continue

            if active_plan is not None and session.uses_interactive_advisor():
                review = _request_advisor_review(
                    session,
                    checkpoint="plan_drift",
                    user_prompt=prompt,
                    candidate_text=_candidate_text_for_checkpoint(
                        response.text,
                        pending_tool_names=pending_tool_names,
                        fallback_label="Candidate response under active execution plan",
                    ),
                    pending_tool_names=pending_tool_names,
                    sink=sink,
                    active_plan=active_plan,
                )
                if _should_request_main_model_revision(review):
                    plan_drifted = True
                    write_constraints_active = True
                    if execution_task_id is not None:
                        session.update_execution_task(
                            execution_task_id,
                            "Revising work after plan drift review",
                            plan_execution_phase="revising",
                            plan_status="drifted",
                            drift_status=review.status,
                            drift_reason=review.reason or "Advisor detected drift from the active plan.",
                            constraint_source=f"plan_drift_{review.status}",
                        )
                    session.activate_execution_constraint(
                        mode="read-only",
                        source=f"plan_drift_{review.status}",
                        reason=review.reason or "Advisor detected drift from the active execution plan.",
                        increment=True,
                    )
                    session.state.messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": session.build_advisor_followup_prompt(
                                        checkpoint="plan_drift",
                                        advisor_review=review,
                                        pending_tool_names=pending_tool_names,
                                        active_plan=active_plan,
                                    ),
                                }
                            ],
                        }
                    )
                    _enforce_message_budget(session, sink)
                    continue

            if not response.tool_calls:
                if final_text and session.has_advisor_model():
                    revised_text = _review_final_text_with_advisor(
                        session,
                        prompt=prompt,
                        draft_text=final_text,
                        sink=sink,
                    )
                    if revised_text:
                        final_text = revised_text
                        assistant_message["content"] = [{"type": "text", "text": revised_text}]
                if final_text and not streamed_text:
                    sink(RuntimeEvent(kind="assistant_text", message=final_text))
                if execution_task_id is not None:
                    session.complete_execution_task(
                        execution_task_id,
                        final_text or "(no output)",
                        plan_execution_phase="completed",
                        plan_status="drifted" if plan_drifted else "on-plan",
                        drift_status=session.state.last_plan_drift_status if plan_drifted else None,
                        constraint_source=session.state.constraint_source,
                    )
                return final_text
            if response.text and not streamed_text:
                sink(RuntimeEvent(kind="assistant_text", message=response.text))

            if session.uses_interactive_advisor() and any(
                tool_name in {"write_file", "edit_file", "apply_patch"}
                for tool_name in pending_tool_names
            ):
                review = _request_advisor_review(
                    session,
                    checkpoint="before_write",
                    user_prompt=prompt,
                    candidate_text=_candidate_text_for_checkpoint(
                        response.text,
                        pending_tool_names=pending_tool_names,
                        fallback_label="Pending write-capable tool calls",
                    ),
                    pending_tool_names=pending_tool_names,
                    sink=sink,
                )
                if _should_request_main_model_revision(review):
                    write_constraints_active = True
                    if execution_task_id is not None:
                        session.update_execution_task(
                            execution_task_id,
                            "Holding write tools until advisor concerns are resolved",
                            plan_execution_phase="revising",
                            plan_status="drifted" if plan_drifted else "on-plan",
                            constraint_source=f"before_write_{review.status}",
                        )
                    session.activate_execution_constraint(
                        mode="read-only",
                        source=f"before_write_{review.status}",
                        reason=review.reason or "Advisor requested a safer non-mutating revision before write tools may run.",
                        increment=True,
                    )
                    session.state.messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": session.build_advisor_followup_prompt(
                                        checkpoint="before_write",
                                        advisor_review=review,
                                        pending_tool_names=pending_tool_names,
                                    ),
                                }
                            ],
                        }
                    )
                    _enforce_message_budget(session, sink)
                    continue
                session.clear_execution_constraint()
                write_constraints_active = False
                if execution_task_id is not None:
                    session.update_execution_task(
                        execution_task_id,
                        "Advisor approved tool execution under active plan",
                        plan_execution_phase="tool_loop",
                        plan_status="drifted" if plan_drifted else "on-plan",
                        constraint_source=None,
                    )

            tool_rounds += 1
            if tool_rounds > session.config.max_tool_rounds_per_turn:
                raise RuntimeError(
                    "Tool round limit exceeded: "
                    f"{tool_rounds} > {session.config.max_tool_rounds_per_turn}"
                )
            tool_names = ", ".join(tool_call.name for tool_call in response.tool_calls)
            sink(
                RuntimeEvent(
                    kind="assistant_tool_call",
                    message=f"calling {len(response.tool_calls)} tool(s): {tool_names}",
                )
            )
            if execution_task_id is not None:
                session.update_execution_task(
                    execution_task_id,
                    f"Running tool round under active plan: {tool_names}",
                    plan_execution_phase="tool_loop",
                    plan_status="drifted" if plan_drifted else "on-plan",
                )

            with _turn_execution_scope(session, write_constraints_active=write_constraints_active):
                tool_batch_result = normalize_tool_batch_execution_result(
                    session.execute_tool_calls(
                        response.tool_calls,
                        session.tool_context(),
                        sink=sink,
                    )
                )
            tool_result_blocks = tool_batch_result.tool_result_blocks
            if tool_batch_result.session_mutations:
                approved_plan_mode_exit_state = _apply_session_tool_mutations(
                    session,
                    tool_result_blocks=tool_result_blocks,
                    session_mutations=tool_batch_result.session_mutations,
                    sink=sink,
                )
            _emit_estimated_tool_result_usage_event(
                session,
                response,
                tool_result_blocks=tool_result_blocks,
                sink=sink,
            )
            session.state.messages.append({"role": "user", "content": tool_result_blocks})
            if tool_batch_result.new_messages:
                session.state.messages.extend(tool_batch_result.new_messages)
                _emit_skill_tool_message_events(
                    tool_batch_result.new_messages,
                    sink=sink,
                )
            if tool_batch_result.context_updates:
                session.apply_transient_tool_context_updates(tool_batch_result.context_updates)
                _emit_skill_tool_context_events(tool_batch_result.context_updates, sink=sink)
            _enforce_message_budget(session, sink)
            for block in tool_result_blocks:
                status = "error" if block.get("is_error") else "ok"
                sink(
                    RuntimeEvent(
                        kind="tool_result",
                        message=status,
                        tool_call_id=block["tool_use_id"],
                        is_error=bool(block.get("is_error")),
                    )
                )
            sink(
                RuntimeEvent(
                    kind="tool_result_summarized",
                    message=_summarize_tool_result_blocks(tool_result_blocks),
                    result_count=len(tool_result_blocks),
                    is_error=any(bool(block.get("is_error")) for block in tool_result_blocks),
                )
            )
            sink(
                RuntimeEvent(
                    kind="assistant_tool_result_ready",
                    message=f"received {len(tool_result_blocks)} tool result block(s); continuing assistant response",
                )
            )
    except Exception as exc:
        if execution_task_id is not None:
            session.fail_execution_task(
                execution_task_id,
                f"{type(exc).__name__}: {exc}",
                plan_execution_phase="failed",
                plan_status="drifted" if plan_drifted else "on-plan",
                drift_status=session.state.last_plan_drift_status if plan_drifted else None,
                constraint_source=session.state.constraint_source,
            )
        del session.state.messages[start_message_count:]
        session.state.context_summary = start_context_summary
        session.state.plan_execution_count = start_plan_execution_count
        session.state.plan_drift_count = start_plan_drift_count
        session.state.last_plan_drift_status = start_last_plan_drift_status
        session.state.last_plan_drift_reason = start_last_plan_drift_reason
        session.state.last_plan_drift_context = start_last_plan_drift_context
        session.state.active_execution_constraint = start_active_execution_constraint
        session.state.constraint_source = start_constraint_source
        session.state.constraint_reason = start_constraint_reason
        session.state.constraint_trigger_count = start_constraint_trigger_count
        session.state.tool_result_replacement_records = list(start_tool_result_replacement_records)
        session.state.tool_result_artifact_records = list(start_tool_result_artifact_records)
        session.restore_transient_tool_context_overlay(start_transient_tool_context_policy)
        session.restore_transient_runtime_override(start_transient_runtime_override)
        session.state.session_runtime_mode = start_session_runtime_mode
        session.state.pre_plan_mode = start_pre_plan_mode
        session.state.has_exited_plan_mode = start_has_exited_plan_mode
        session.state.needs_plan_mode_exit_attachment = start_needs_plan_mode_exit_attachment
        session.state.needs_plan_mode_reentry_attachment = start_needs_plan_mode_reentry_attachment
        session.state.plan_mode_attachment_count = start_plan_mode_attachment_count
        session._plan_mode_attachment_count = start_plan_mode_attachment_count
        session.state.plan_mode_exit_approved_plan = start_plan_mode_exit_approved_plan
        session.state.plan_mode_exit_restored_mode = start_plan_mode_exit_restored_mode
        session.state.plan_slug = start_plan_slug
        if approved_plan_mode_exit_state is not None:
            session.state.session_runtime_mode = str(
                approved_plan_mode_exit_state["session_runtime_mode"]
            )
            session.state.pre_plan_mode = approved_plan_mode_exit_state["pre_plan_mode"]  # type: ignore[assignment]
            session.state.has_exited_plan_mode = bool(
                approved_plan_mode_exit_state["has_exited_plan_mode"]
            )
            session.state.needs_plan_mode_exit_attachment = bool(
                approved_plan_mode_exit_state["needs_plan_mode_exit_attachment"]
            )
            session.state.needs_plan_mode_reentry_attachment = bool(
                approved_plan_mode_exit_state["needs_plan_mode_reentry_attachment"]
            )
            session.state.plan_mode_attachment_count = int(
                approved_plan_mode_exit_state["plan_mode_attachment_count"]
            )
            session._plan_mode_attachment_count = session.state.plan_mode_attachment_count
            session.state.plan_mode_exit_approved_plan = approved_plan_mode_exit_state[
                "plan_mode_exit_approved_plan"
            ]  # type: ignore[assignment]
            session.state.plan_mode_exit_restored_mode = approved_plan_mode_exit_state[
                "plan_mode_exit_restored_mode"
            ]  # type: ignore[assignment]
            session.state.plan_slug = approved_plan_mode_exit_state["plan_slug"]  # type: ignore[assignment]
        session.reconstruct_tool_result_replacement_state()
        if session.persist_transcript:
            session.persist_state()
        raise
    finally:
        session.restore_transient_tool_context_overlay(start_transient_tool_context_policy)
        session.restore_transient_runtime_override(start_transient_runtime_override)
        session.clear_plan_execution()
        session.clear_execution_constraint()

    raise RuntimeError("Max turn count reached.")


def _apply_session_tool_mutations(
    session: "Session",
    *,
    tool_result_blocks: list[dict[str, object]],
    session_mutations,
    sink: EventSink,
) -> dict[str, object] | None:
    approved_exit_state: dict[str, object] | None = None
    for mutation in session_mutations:
        if mutation.kind != "plan_mode_exit_requested":
            continue
        approved_exit_state = _handle_plan_mode_exit_request(
            session,
            tool_result_blocks=tool_result_blocks,
            mutation=mutation,
            sink=sink,
        )
    return approved_exit_state


def _handle_plan_mode_exit_request(
    session: "Session",
    *,
    tool_result_blocks: list[dict[str, object]],
    mutation,
    sink: EventSink,
) -> dict[str, object] | None:
    tool_call_id = mutation.source_tool_call_id
    if tool_call_id is None:
        return None
    plan_file_path = Path(str(mutation.plan_file_path or "")).resolve(strict=False)
    plan_content = str(mutation.plan_content or "").strip()
    block = next(
        (
            item
            for item in tool_result_blocks
            if str(item.get("type")) == "tool_result" and item.get("tool_use_id") == tool_call_id
        ),
        None,
    )
    if block is None:
        return None
    if not plan_content:
        block["is_error"] = True
        block["content"] = "Current session plan file is empty."
        return None
    try:
        session.request_plan_mode_exit_approval(
            plan_file_path=plan_file_path,
            plan_content=plan_content,
        )
    except PermissionDeniedError:
        block["is_error"] = True
        block["content"] = (
            "The user rejected the current plan and chose to stay in plan mode.\n"
            f"plan_file: {plan_file_path}\n\n"
            "Rejected plan:\n"
            f"{plan_content}"
        )
        return None
    session.exit_plan_mode(approved_plan=plan_content)
    block["is_error"] = False
    block["content"] = (
        "Plan mode exit approved.\n"
        f"plan_file: {plan_file_path}\n\n"
        "## Approved Plan:\n"
        f"{plan_content}"
    )
    return {
        "session_runtime_mode": session.state.session_runtime_mode,
        "pre_plan_mode": session.state.pre_plan_mode,
        "has_exited_plan_mode": session.state.has_exited_plan_mode,
        "needs_plan_mode_exit_attachment": session.state.needs_plan_mode_exit_attachment,
        "needs_plan_mode_reentry_attachment": session.state.needs_plan_mode_reentry_attachment,
        "plan_mode_attachment_count": session.state.plan_mode_attachment_count,
        "plan_mode_exit_approved_plan": session.state.plan_mode_exit_approved_plan,
        "plan_mode_exit_restored_mode": session.state.plan_mode_exit_restored_mode,
        "plan_slug": session.state.plan_slug,
    }


def _emit_skill_tool_message_events(
    new_messages: list[dict[str, object]],
    *,
    sink: EventSink,
) -> None:
    message_counts: dict[tuple[str, str], int] = {}
    for message in new_messages:
        source_kind = str(message.get("source_kind") or "").strip()
        if source_kind not in {"skill_tool_inline", "skill_tool_fork"}:
            continue
        skill_name = str(message.get("skill_name") or "").strip()
        if not skill_name:
            continue
        key = (source_kind, skill_name)
        message_counts[key] = message_counts.get(key, 0) + 1
    event_kinds = {
        "skill_tool_inline": ("skill_tool_inline_messages_applied", "skill-tool inline"),
        "skill_tool_fork": ("skill_tool_fork_messages_applied", "skill-tool fork"),
    }
    for (source_kind, skill_name), count in message_counts.items():
        event_kind, label = event_kinds[source_kind]
        sink(
            RuntimeEvent(
                kind=event_kind,  # type: ignore[arg-type]
                message=f"{label}: {skill_name} injected_messages={count}",
                tool_name="skill",
                result_count=count,
            )
        )


def _emit_skill_tool_context_events(context_updates, *, sink: EventSink) -> None:
    for update in context_updates:
        skill_name = str(getattr(update, "skill_name", "") or "").strip()
        if str(getattr(update, "source", "") or "").strip() not in {
            "skill_tool_inline",
            "skill_tool_fork",
        } or not skill_name:
            continue
        allowed_tools = getattr(update, "allowed_tool_names", None)
        allowed_summary = ",".join(allowed_tools or ()) or "inherit"
        sink(
            RuntimeEvent(
                kind="skill_tool_context_applied",
                message=f"skill-tool context: {skill_name} allowed_tools={allowed_summary}",
                tool_name="skill",
            )
        )


def _emit_response_usage_event(session: "Session", response, sink: EventSink) -> None:
    usage = getattr(response, "usage", None)
    if usage is not None and usage.total_tokens is not None:
        event = RuntimeEvent(
            kind="assistant_usage",
            message="provider usage",
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            usage_source="provider",
        )
        sink(event)
        session._record_runtime_usage_event(event)
        return
    estimated_total = _estimate_response_tokens(response)
    if estimated_total <= 0:
        return
    event = RuntimeEvent(
        kind="assistant_usage",
        message="estimated response usage",
        total_tokens=estimated_total,
        usage_source="estimated",
    )
    sink(event)
    session._record_runtime_usage_event(event)


def _emit_estimated_tool_result_usage_event(
    session: "Session",
    response,
    *,
    tool_result_blocks: list[dict],
    sink: EventSink,
) -> None:
    usage = getattr(response, "usage", None)
    if usage is not None and usage.total_tokens is not None:
        return
    estimated_total = sum(_estimate_tool_result_block_tokens(block) for block in tool_result_blocks)
    if estimated_total <= 0:
        return
    event = RuntimeEvent(
        kind="assistant_usage",
        message="estimated tool result usage",
        total_tokens=estimated_total,
        usage_source="estimated",
    )
    sink(event)
    session._record_runtime_usage_event(event)


def _estimate_response_tokens(response) -> int:
    total = 0
    text = str(getattr(response, "text", "") or "").strip()
    if text:
        total += _estimate_token_count(text)
    for tool_call in getattr(response, "tool_calls", []) or []:
        total += _estimate_token_count(summarize_tool_input(tool_call.input))
    return total


def _estimate_tool_result_block_tokens(block: dict[str, object]) -> int:
    if not isinstance(block, dict):
        return 0
    parts: list[str] = []
    if block.get("is_error"):
        parts.append("error")
    content = str(block.get("content", "") or "").strip()
    if content:
        parts.append(content[:160])
    return _estimate_token_count(" ".join(parts))


def _estimate_token_count(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    return (len(stripped) + 3) // 4


def _normalize_context_limit_reason(exc: ProviderContextLimitError, limit: int = 160) -> str:
    message = " ".join(str(exc).strip().split())
    if not message:
        return "prompt-too-long: provider context limit"
    prefix = "prompt-too-long: "
    available = max(limit - len(prefix), 1)
    if len(message) > available:
        message = message[: available - 3].rstrip() + "..."
    return prefix + message


def _emit_prompt_prefix_planner_events(
    session: "Session",
    cache_plan,
    sink: EventSink,
) -> None:
    prefix_plan = getattr(cache_plan, "prefix_plan", None)
    if prefix_plan is None:
        return
    summary = (
        f"tier={prefix_plan.selected_reduction_tier} "
        f"reason={prefix_plan.planner_reason} "
        f"preserved_groups={prefix_plan.preserved_message_group_count} "
        f"downgraded_groups={prefix_plan.downgraded_message_group_count}"
    )
    if session.prompt_prefix_planner_downgraded(prefix_plan):
        sink(
            RuntimeEvent(
                kind="prompt_prefix_planner_downgraded",
                message=summary,
                replacement_reason=prefix_plan.planner_reason,
            )
        )
        return
    if (
        prefix_plan.previous_preserved_signature is None
        or prefix_plan.previous_preserved_signature != prefix_plan.preserved_prefix_signature
    ):
        sink(
            RuntimeEvent(
                kind="prompt_prefix_planner_applied",
                message=summary,
                replacement_reason=prefix_plan.planner_reason,
            )
        )


def _create_message_with_retries(
    session: "Session",
    sink: EventSink,
    *,
    turn_user_prompt: str | None = None,
    messages_override: list[dict] | None = None,
    assembly_override=None,
    cache_plan_override=None,
):
    if assembly_override is not None:
        cache_plan = cache_plan_override or session.build_provider_prompt_cache_plan(
            assembly_override,
            previous_payload=session._last_prompt_prefix_assembly_payload,
        )
        runtime_override = session.current_runtime_override()
        session.mark_tool_result_ids_seen_from_messages(assembly_override.messages)
        response, streamed_text = _create_provider_message_with_retries(
            session.provider,
            session=session,
            messages=assembly_override.messages,
            tools=assembly_override.tools,
            system_prompt=assembly_override.system_prompt,
            cache_plan=cache_plan,
            model_override=runtime_override.model_override if runtime_override is not None else None,
            effort_override=runtime_override.effort_override if runtime_override is not None else None,
            sink=sink,
            allow_streaming=not session.has_advisor_model(),
        )
        session.record_prompt_prefix_assembly(
            assembly_override,
            source="runtime",
            cache_plan=cache_plan,
        )
        session.record_plan_mode_attachment_cycle(assembly_override.prompt_attachments)
        consumed_attachment_kinds = tuple(
            attachment.kind
            for attachment in assembly_override.prompt_attachments
            if attachment.one_shot
        )
        if consumed_attachment_kinds:
            session.mark_plan_mode_attachments_consumed(consumed_attachment_kinds)
        if cache_plan.provider_cache_fallback_reason not in {None, "", "none"}:
            sink(
                RuntimeEvent(
                    kind="prompt_cache_hints_fallback",
                    message=(
                        f"provider={cache_plan.provider_cache_provider} "
                        f"reason={cache_plan.provider_cache_fallback_reason}"
                    ),
                    is_error=True,
                )
            )
        elif cache_plan.provider_cache_mode == "provider_hinted":
            sink(
                RuntimeEvent(
                    kind="prompt_cache_hints_applied",
                    message=(
                        f"provider={cache_plan.provider_cache_provider} "
                        f"summary={cache_plan.provider_cache_summary}"
                    ),
                )
            )
        _emit_prompt_prefix_planner_events(session, cache_plan, sink)
        return response, streamed_text
    messages = messages_override or _messages_for_provider_call(
        session,
        turn_user_prompt=turn_user_prompt,
    )
    runtime_override = session.current_runtime_override()
    session.mark_tool_result_ids_seen_from_messages(messages)
    return _create_provider_message_with_retries(
        session.provider,
        session=session,
        messages=messages,
        tools=session.tool_specs(),
        system_prompt=session.build_system_prompt(),
        model_override=runtime_override.model_override if runtime_override is not None else None,
        effort_override=runtime_override.effort_override if runtime_override is not None else None,
        sink=sink,
        allow_streaming=not session.has_advisor_model(),
    )


def _create_message_with_compact_recovery(
    session: "Session",
    sink: EventSink,
    *,
    turn_user_prompt: str | None = None,
):
    attempted_recovery = False
    while True:
        orchestration = _orchestrated_provider_view(
            session,
            turn_user_prompt=turn_user_prompt,
        )
        _persist_and_emit_tool_result_replacement_events(
            session,
            orchestration.final_reduction_result,
            sink,
        )
        compacted = _enforce_message_budget(
            session,
            sink,
            messages_override=orchestration.final_messages,
        )
        if compacted:
            orchestration = _orchestrated_provider_view(
                session,
                turn_user_prompt=turn_user_prompt,
            )
            _persist_and_emit_tool_result_replacement_events(
                session,
                orchestration.final_reduction_result,
                sink,
            )
        assembly = orchestration.final_assembly
        cache_plan = orchestration.final_cache_plan
        if compacted:
            assembly = session.build_provider_prompt_assembly(
                messages=orchestration.final_messages,
                replacement_result=orchestration.final_reduction_result,
                compacted_before_provider=True,
            )
            cache_plan = session.build_provider_prompt_cache_plan(
                assembly,
                previous_payload=session._last_prompt_prefix_assembly_payload,
            )
            _copy_orchestration_summary(orchestration, cache_plan)
        try:
            return _create_message_with_retries(
                session,
                sink,
                assembly_override=assembly,
                cache_plan_override=cache_plan,
            )
        except ProviderContextLimitError as exc:
            if attempted_recovery:
                raise
            preview = session._history_compaction_preview_payload()
            budget = session.refresh_runtime_budget_state(
                preview=preview,
                message_override=orchestration.final_messages,
            )
            if not bool(budget.get("would_compact")) or not bool(preview.get("would_compact")):
                raise
            attempted_recovery = True
            sink(
                RuntimeEvent(
                    kind="provider_retry",
                    message=(
                        "ProviderContextLimitError: compacting and retrying turn after "
                        "prompt-too-long (attempt 1/1)"
                    ),
                    is_error=True,
                )
            )
            reason = _normalize_context_limit_reason(exc)
            sink(
                RuntimeEvent(
                    kind="compact_recovery_started",
                    message="starting compact recovery after prompt-too-long",
                    compaction_trigger="recovery",
                    budget_state=str(budget.get("budget_state") or "ok"),
                    budget_reason=str(budget.get("budget_reason") or ""),
                    is_error=True,
                )
            )
            _compact_history(
                session,
                sink,
                trigger_reason=reason,
                trigger="recovery",
            )
            refreshed_orchestration = _orchestrated_provider_view(
                session,
                turn_user_prompt=turn_user_prompt,
            )
            _persist_and_emit_tool_result_replacement_events(
                session,
                refreshed_orchestration.final_reduction_result,
                sink,
            )
            refreshed = session.refresh_runtime_budget_state(
                message_override=refreshed_orchestration.final_messages
            )
            refreshed_state = str(refreshed.get("budget_state") or "ok")
            if refreshed_state in {"compact_needed", "hard_stop"}:
                sink(
                    RuntimeEvent(
                        kind="compact_recovery_finished",
                        message="compact recovery failed to restore budget headroom",
                        compaction_trigger="recovery",
                        budget_state=refreshed_state,
                        budget_reason=str(refreshed.get("budget_reason") or ""),
                        is_error=True,
                    )
                )
                raise RuntimeError(
                    "Prompt-too-long recovery failed after compaction: "
                    f"{refreshed.get('budget_reason') or 'budget limits exceeded'}"
                ) from exc
            sink(
                RuntimeEvent(
                    kind="compact_recovery_finished",
                    message="compact recovery restored budget headroom; retrying turn",
                    compaction_trigger="recovery",
                    budget_state=refreshed_state,
                    budget_reason=str(refreshed.get("budget_reason") or ""),
                    is_error=False,
                )
            )


def _create_provider_message_with_retries(
    provider,
    *,
    session: "Session",
    messages: list[dict],
    tools: list[dict],
    system_prompt: str,
    cache_plan=None,
    model_override: str | None = None,
    effort_override: str | None = None,
    sink: EventSink,
    allow_streaming: bool,
):
    max_retries = session.config.provider_max_retries
    base_delay = session.config.provider_retry_base_delay_sec

    for attempt in range(max_retries + 1):
        try:
            return _create_provider_message_once(
                provider,
                messages=messages,
                tools=tools,
                system_prompt=system_prompt,
                cache_plan=cache_plan,
                model_override=model_override,
                effort_override=effort_override,
                sink=sink,
                allow_streaming=allow_streaming,
            )
        except (ProviderRateLimitError, ProviderTimeoutError, ProviderNetworkError) as exc:
            if attempt >= max_retries:
                raise
            delay = base_delay * (2**attempt)
            sink(
                RuntimeEvent(
                    kind="provider_retry",
                    message=f"{type(exc).__name__}: retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})",
                    is_error=True,
                )
            )
            if delay > 0:
                sleep(delay)
        except ProviderCapabilityError:
            raise


def _create_message_once(session: "Session", sink: EventSink):
    orchestration = _orchestrated_provider_view(session)
    _persist_and_emit_tool_result_replacement_events(
        session,
        orchestration.final_reduction_result,
        sink,
    )
    assembly = orchestration.final_assembly
    cache_plan = orchestration.final_cache_plan
    runtime_override = session.current_runtime_override()
    session.mark_tool_result_ids_seen_from_messages(assembly.messages)
    response, streamed_text = _create_provider_message_once(
        session.provider,
        messages=assembly.messages,
        tools=assembly.tools,
        system_prompt=assembly.system_prompt,
        cache_plan=cache_plan,
        model_override=runtime_override.model_override if runtime_override is not None else None,
        effort_override=runtime_override.effort_override if runtime_override is not None else None,
        sink=sink,
        allow_streaming=not session.has_advisor_model(),
    )
    session.record_prompt_prefix_assembly(assembly, source="runtime", cache_plan=cache_plan)
    session.record_plan_mode_attachment_cycle(assembly.prompt_attachments)
    consumed_attachment_kinds = tuple(
        attachment.kind for attachment in assembly.prompt_attachments if attachment.one_shot
    )
    if consumed_attachment_kinds:
        session.mark_plan_mode_attachments_consumed(consumed_attachment_kinds)
    if cache_plan.provider_cache_fallback_reason not in {None, "", "none"}:
        sink(
            RuntimeEvent(
                kind="prompt_cache_hints_fallback",
                message=(
                    f"provider={cache_plan.provider_cache_provider} "
                    f"reason={cache_plan.provider_cache_fallback_reason}"
                ),
                is_error=True,
            )
        )
    elif cache_plan.provider_cache_mode == "provider_hinted":
        sink(
            RuntimeEvent(
                kind="prompt_cache_hints_applied",
                message=(
                    f"provider={cache_plan.provider_cache_provider} "
                    f"summary={cache_plan.provider_cache_summary}"
                ),
            )
        )
    _emit_prompt_prefix_planner_events(session, cache_plan, sink)
    return response, streamed_text


def _create_provider_message_once(
    provider,
    *,
    messages: list[dict],
    tools: list[dict],
    system_prompt: str,
    cache_plan=None,
    model_override: str | None = None,
    effort_override: str | None = None,
    sink: EventSink,
    allow_streaming: bool,
):
    capabilities = getattr(provider, "capabilities", None)
    if (
        allow_streaming
        and
        capabilities is not None
        and capabilities.supports_streaming
        and hasattr(provider, "stream_message")
    ):
        response = None
        streamed_text = False
        for event in _stream_provider_message(
            provider,
            messages=messages,
            tools=tools,
            system_prompt=system_prompt,
            cache_plan=cache_plan,
            model_override=model_override,
            effort_override=effort_override,
        ):
            if event.kind == "text_delta" and event.text:
                streamed_text = True
                sink(RuntimeEvent(kind="assistant_text", message=event.text))
                continue
            if event.kind == "response":
                response = event.response
        if response is None:
            raise RuntimeError("Provider stream ended without a final response.")
        return response, streamed_text

    return (
        _create_provider_message(
            provider,
            messages=messages,
            tools=tools,
            system_prompt=system_prompt,
            cache_plan=cache_plan,
            model_override=model_override,
            effort_override=effort_override,
        ),
        False,
    )


def _create_provider_message(
    provider,
    *,
    messages,
    tools,
    system_prompt,
    cache_plan,
    model_override,
    effort_override,
):
    return _call_provider_with_optional_kwargs(
        provider,
        "create_message",
        messages=messages,
        tools=tools,
        system_prompt=system_prompt,
        cache_plan=cache_plan,
        model_override=model_override,
        effort_override=effort_override,
    )


def _stream_provider_message(
    provider,
    *,
    messages,
    tools,
    system_prompt,
    cache_plan,
    model_override,
    effort_override,
):
    return _call_provider_with_optional_kwargs(
        provider,
        "stream_message",
        messages=messages,
        tools=tools,
        system_prompt=system_prompt,
        cache_plan=cache_plan,
        model_override=model_override,
        effort_override=effort_override,
    )


def _call_provider_with_optional_kwargs(provider, method_name: str, **kwargs):
    method = getattr(provider, method_name)
    base_kwargs = {
        "messages": kwargs["messages"],
        "tools": kwargs["tools"],
        "system_prompt": kwargs["system_prompt"],
    }
    optional_kwargs = {
        name: value
        for name, value in (
            ("cache_plan", kwargs.get("cache_plan")),
            ("model_override", kwargs.get("model_override")),
            ("effort_override", kwargs.get("effort_override")),
        )
        if value is not None
    }
    while True:
        try:
            return method(**base_kwargs, **optional_kwargs)
        except TypeError as exc:
            message = str(exc)
            unsupported_name = next(
                (name for name in tuple(optional_kwargs) if name in message),
                None,
            )
            if unsupported_name is None:
                raise
            optional_kwargs.pop(unsupported_name, None)


def _review_final_text_with_advisor(
    session: "Session",
    *,
    prompt: str,
    draft_text: str,
    sink: EventSink,
) -> str | None:
    session.run_plugin_hooks("before_final_answer", prompt=prompt, draft_text=draft_text)
    review = _request_advisor_review(
        session,
        checkpoint="final_answer",
        user_prompt=prompt,
        candidate_text=draft_text,
        pending_tool_names=(),
        sink=sink,
    )
    if review is None or review.status == "approve":
        return None
    try:
        sink(
            RuntimeEvent(
                kind="advisor_revision_requested",
                message=f"checkpoint=final_answer status={review.status}",
            )
        )
        revised_response, _ = _create_provider_message_with_retries(
            session.provider,
            session=session,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": session.build_advisor_revision_prompt(
                                user_prompt=prompt,
                                draft_text=draft_text,
                                advisor_feedback=_format_advisor_feedback(review),
                            ),
                        }
                    ],
                }
            ],
            tools=[],
            system_prompt=session.build_system_prompt(),
            sink=sink,
            allow_streaming=False,
        )
        revised_text = revised_response.text.strip()
        if not revised_text:
            sink(
                RuntimeEvent(
                    kind="advisor_error",
                    message="revision returned empty response; keeping draft",
                    is_error=True,
                )
            )
            return None
        sink(RuntimeEvent(kind="advisor_revision_requested", message="applied revised final answer"))
        return revised_text
    except Exception as exc:  # noqa: BLE001
        sink(
            RuntimeEvent(
                kind="advisor_error",
                message=f"{type(exc).__name__}: advisor review failed; keeping draft",
                is_error=True,
            )
        )
        return None


def _request_advisor_review(
    session: "Session",
    *,
    checkpoint: str,
    user_prompt: str,
    candidate_text: str,
    pending_tool_names: tuple[str, ...],
    sink: EventSink,
    active_plan=None,
):
    from ..state import AdvisorReviewSummary

    advisor_provider = session.build_advisor_provider()
    if advisor_provider is None:
        return None
    try:
        plan_drift_context = None
        if checkpoint == "plan_drift" and active_plan is not None:
            plan_drift_context = session.build_plan_drift_review_context(
                active_plan=active_plan,
                candidate_text=candidate_text,
                pending_tool_names=pending_tool_names,
            )
            session.record_plan_drift_context(plan_drift_context)
        sink(
            RuntimeEvent(
                kind="advisor_review_started",
                message=f"checkpoint={checkpoint} model={session.state.advisor_model}",
            )
        )
        sink(
            RuntimeEvent(
                kind="advisor",
                message=f"reviewing {checkpoint} with {session.state.advisor_model}",
            )
        )
        review_response, _ = _create_provider_message_with_retries(
            advisor_provider,
            session=session,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": session.build_advisor_review_prompt(
                                checkpoint=checkpoint,
                                user_prompt=user_prompt,
                                candidate_text=candidate_text,
                                pending_tool_names=pending_tool_names,
                                active_plan=active_plan,
                                plan_drift_context=plan_drift_context,
                            ),
                        }
                    ],
                }
            ],
            tools=[],
            system_prompt=(
                "You are the advisor model for a coding assistant. "
                "Review the candidate work and return a strict JSON review object."
            ),
            sink=sink,
            allow_streaming=False,
        )
        review = _parse_advisor_review(
            review_response.text,
            checkpoint=checkpoint,
            model=session.state.advisor_model or "",
        )
        session.record_advisor_review(review)
        sink(
            RuntimeEvent(
                kind="advisor_review_result",
                message=_format_advisor_result_message(review),
                is_error=review.status == "block",
            )
        )
        sink(RuntimeEvent(kind="advisor", message=_format_advisor_result_message(review), is_error=review.status == "block"))
        if review.status != "approve":
            sink(
                RuntimeEvent(
                    kind="advisor_revision_requested",
                    message=f"checkpoint={checkpoint} status={review.status}",
                    is_error=review.status == "block",
                )
            )
            sink(
                RuntimeEvent(
                    kind="advisor",
                    message=f"checkpoint={checkpoint} requested revision ({review.status})",
                    is_error=review.status == "block",
                )
            )
        return review
    except Exception as exc:  # noqa: BLE001
        sink(
            RuntimeEvent(
                kind="advisor_error",
                message=f"{type(exc).__name__}: advisor review failed",
                is_error=True,
            )
        )
        sink(
            RuntimeEvent(
                kind="advisor",
                message=f"{type(exc).__name__}: advisor review failed",
                is_error=True,
            )
        )
        return None


def _parse_advisor_feedback(text: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None
    normalized = stripped.upper()
    if normalized == "APPROVED" or normalized.startswith("APPROVED\n") or normalized.startswith("APPROVED:"):
        return None
    if normalized.startswith("REVISE"):
        feedback = stripped[len("REVISE") :].lstrip(" :\n\t")
        return feedback or None
    return stripped


def _parse_advisor_review(text: str, *, checkpoint: str, model: str):
    from ..state import AdvisorReviewSummary

    stripped = text.strip()
    payload: dict[str, object] | None = None
    if stripped:
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
    if isinstance(payload, dict):
        status = str(payload.get("status", "approve")).strip().lower()
        if status not in {"approve", "revise", "block"}:
            status = "revise"
        reason = str(payload.get("reason", "") or "").strip()
        suggested_changes = [
            str(item).strip()
            for item in (payload.get("suggested_changes") or [])
            if str(item).strip()
        ]
        risk_flags = [
            str(item).strip()
            for item in (payload.get("risk_flags") or [])
            if str(item).strip()
        ]
        return AdvisorReviewSummary(
            checkpoint=checkpoint,
            status=status,
            reason=reason,
            suggested_changes=suggested_changes,
            risk_flags=risk_flags,
            model=model,
        )

    feedback = _parse_advisor_feedback(stripped)
    status = "approve" if feedback is None else "revise"
    return AdvisorReviewSummary(
        checkpoint=checkpoint,
        status=status,
        reason=feedback or "",
        suggested_changes=[feedback] if feedback else [],
        risk_flags=[],
        model=model,
    )


def _format_advisor_feedback(review) -> str:
    parts = [f"status={review.status}"]
    if review.reason:
        parts.append("reason: " + review.reason)
    if review.suggested_changes:
        parts.append("suggested changes:\n" + "\n".join(f"- {item}" for item in review.suggested_changes))
    if review.risk_flags:
        parts.append("risk flags:\n" + "\n".join(f"- {item}" for item in review.risk_flags))
    return "\n\n".join(parts)


def _format_advisor_result_message(review) -> str:
    message = f"checkpoint={review.checkpoint} status={review.status}"
    if review.risk_flags:
        message += " risk_flags=" + ",".join(review.risk_flags)
    if review.reason:
        message += f" reason={review.reason}"
    return message


def _should_request_main_model_revision(review) -> bool:
    return review is not None and review.status in {"revise", "block"}


def _candidate_text_for_checkpoint(
    draft_text: str,
    *,
    pending_tool_names: tuple[str, ...],
    fallback_label: str,
) -> str:
    stripped = draft_text.strip()
    if stripped:
        return stripped
    if pending_tool_names:
        return f"{fallback_label}: {', '.join(pending_tool_names)}"
    return fallback_label


def _messages_for_provider_call(session: "Session", *, turn_user_prompt: str | None = None) -> list[dict]:
    if not turn_user_prompt:
        return session.state.messages
    messages = list(session.state.messages)
    if not messages:
        return messages
    latest = messages[-1]
    if latest.get("role") != "user":
        return messages
    content = list(latest.get("content") or [])
    if not content:
        return messages
    first_block = dict(content[0])
    if first_block.get("type") != "text":
        return messages
    first_block["text"] = turn_user_prompt
    content[0] = first_block
    updated = dict(latest)
    updated["content"] = content
    messages[-1] = updated
    return messages


def _replacement_aware_provider_view(
    session: "Session",
    *,
    turn_user_prompt: str | None = None,
) -> ToolResultBudgetResult:
    messages = _messages_for_provider_call(session, turn_user_prompt=turn_user_prompt)
    return apply_tool_result_budget_to_messages(session, messages)


def _orchestrated_provider_view(
    session: "Session",
    *,
    turn_user_prompt: str | None = None,
) -> ProviderViewReductionOrchestrationResult:
    messages = _messages_for_provider_call(session, turn_user_prompt=turn_user_prompt)
    return build_provider_view_reduction_orchestration(
        session,
        raw_messages=messages,
        previous_payload=session._last_prompt_prefix_assembly_payload,
    )


def _copy_orchestration_summary(
    orchestration: ProviderViewReductionOrchestrationResult,
    cache_plan,
) -> None:
    cache_plan.orchestration_mode = orchestration.final_cache_plan.orchestration_mode
    cache_plan.orchestration_reason = orchestration.final_cache_plan.orchestration_reason
    cache_plan.orchestration_selected_candidate_count = (
        orchestration.final_cache_plan.orchestration_selected_candidate_count
    )
    cache_plan.orchestration_selected_candidate_summary = (
        orchestration.final_cache_plan.orchestration_selected_candidate_summary
    )
    cache_plan.orchestration_remaining_estimated_overage = (
        orchestration.final_cache_plan.orchestration_remaining_estimated_overage
    )
    cache_plan.orchestration_requires_full_compaction = (
        orchestration.final_cache_plan.orchestration_requires_full_compaction
    )


def _persist_and_emit_tool_result_replacement_events(
    session: "Session",
    replacement_result: ToolResultBudgetResult,
    sink: EventSink,
) -> None:
    if replacement_result.newly_artifact_records:
        session._record_tool_result_artifact_records(replacement_result.newly_artifact_records)
    if replacement_result.newly_replaced_records:
        session._record_tool_result_replacement_records(replacement_result.newly_replaced_records)
    if replacement_result.newly_artifact_records or replacement_result.newly_replaced_records:
        session.persist_state()
    if replacement_result.newly_artifact_records:
        sink(
            RuntimeEvent(
                kind="tool_result_artifact_created",
                message=(
                    "tool-result artifact: created "
                    f"count={replacement_result.artifact_count} "
                    f"shed_chars={replacement_result.artifact_chars_saved}"
                ),
                artifact_count=replacement_result.artifact_count,
                artifact_chars_saved=replacement_result.artifact_chars_saved,
                replacement_reason=replacement_result.budget_reason,
            )
        )
    if replacement_result.newly_replaced_records:
        sink(
            RuntimeEvent(
                kind="tool_result_replacement_applied",
                message=(
                    "tool-result replacement: applied "
                    f"count={replacement_result.replacement_count} "
                    f"shed_chars={replacement_result.replaced_chars_total}"
                ),
                replacement_count=replacement_result.replacement_count,
                replaced_chars_total=replacement_result.replaced_chars_total,
                replacement_reason=replacement_result.budget_reason,
            )
        )
    elif replacement_result.reapplied_count:
        sink(
            RuntimeEvent(
                kind="tool_result_replacement_reapplied",
                message=(
                    "tool-result replacement: re-applied "
                    f"count={replacement_result.reapplied_count}"
                ),
                replacement_count=replacement_result.reapplied_count,
                replaced_chars_total=replacement_result.replaced_chars_total,
                replacement_reason=replacement_result.budget_reason,
            )
        )
    if replacement_result.artifact_reuse_count:
        sink(
            RuntimeEvent(
                kind="tool_result_artifact_reused",
                message=(
                    "tool-result artifact: re-used "
                    f"count={replacement_result.artifact_reuse_count}"
                ),
                artifact_count=replacement_result.artifact_reuse_count,
                replacement_reason=replacement_result.budget_reason,
            )
        )
    if replacement_result.microcompact_count:
        sink(
            RuntimeEvent(
                kind="tool_result_microcompacted",
                message=(
                    "tool-result microcompact: applied "
                    f"count={replacement_result.microcompact_count} "
                    f"shed_chars={replacement_result.microcompact_chars_saved}"
                ),
                microcompact_count=replacement_result.microcompact_count,
                microcompact_chars_saved=replacement_result.microcompact_chars_saved,
                replacement_reason=replacement_result.budget_reason,
            )
        )


def _read_only_turn_tool_names(session: "Session") -> tuple[str, ...]:
    tool_names = []
    for spec in session.tool_specs():
        name = spec.get("name")
        if not isinstance(name, str):
            continue
        if name in {"write_file", "edit_file", "apply_patch"}:
            continue
        tool_names.append(name)
    return tuple(tool_names)


def _turn_execution_scope(session: "Session", *, write_constraints_active: bool):
    if not write_constraints_active:
        return nullcontext()
    return session._command_execution_scope(
        allowed_tool_names=_read_only_turn_tool_names(session),
        allowed_bash_command_prefixes=(
            "pwd",
            "git status",
            "git diff",
            "git log",
            "git show",
            "git branch",
            "git ls-files",
            "git grep",
            "rg ",
            "grep ",
            "ls",
            "dir",
        ),
        require_read_only_subagents=True,
        command_policy_name="read-only-turn",
        command_policy_source="advisor-read-only-scope",
    )


def _enforce_message_budget(
    session: "Session",
    sink: EventSink,
    *,
    messages_override: list[dict] | None = None,
) -> bool:
    report = (
        estimate_provider_call_context_usage(session, messages_override)
        if messages_override is not None
        else None
    )
    budget = session.refresh_runtime_budget_state(
        report=report,
        message_override=messages_override,
    )
    if session.should_emit_budget_pressure_event(budget):
        sink(
            RuntimeEvent(
                kind="budget_pressure",
                message=str(budget.get("budget_reason") or budget.get("budget_state") or "budget pressure"),
                budget_state=str(budget.get("budget_state") or "ok"),
                budget_reason=str(budget.get("budget_reason") or ""),
                is_error=str(budget.get("budget_state") or "ok") in {"compact_needed", "hard_stop"},
            )
        )
    state = str(budget.get("budget_state") or "ok")
    if state in {"ok", "warning"}:
        return False
    if state == "hard_stop":
        raise RuntimeError(
            "Message budget exceeded without a recoverable compaction path: "
            f"{budget.get('budget_reason') or 'budget limits exceeded'}"
        )
    _compact_history(session, sink, trigger_reason=str(budget.get("budget_reason") or "").strip() or None)
    refreshed = session.refresh_runtime_budget_state()
    if str(refreshed.get("budget_state") or "ok") in {"compact_needed", "hard_stop"}:
        raise RuntimeError(
            "Message budget exceeded after compaction: "
            f"{refreshed.get('budget_reason') or 'budget limits exceeded'}"
        )
    return True


def _summarize_tool_result_blocks(tool_result_blocks: list[dict[str, object]]) -> str:
    result_count = len(tool_result_blocks)
    error_blocks = [block for block in tool_result_blocks if bool(block.get("is_error"))]
    if not error_blocks:
        return f"ok results={result_count}"
    parts = [f"results={result_count}", f"errors={len(error_blocks)}"]
    first_error = str(error_blocks[0].get("content") or "").strip()
    if first_error:
        compact = " ".join(first_error.split())
        if len(compact) > 120:
            compact = compact[:117].rstrip() + "..."
        parts.append(f"first_error={compact}")
    return " ".join(parts)


def _compact_history(
    session: "Session",
    sink: EventSink,
    trigger_reason: str | None = None,
    *,
    trigger: str = "auto",
) -> None:
    session.apply_history_compaction(
        persist=False,
        sink=sink,
        trigger=trigger,
        trigger_reason=trigger_reason,
    )
