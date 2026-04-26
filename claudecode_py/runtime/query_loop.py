from __future__ import annotations

from contextlib import nullcontext
import json
from time import sleep

from ..models import Message
from ..providers.errors import (
    ProviderCapabilityError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from .events import EventSink, RuntimeEvent, null_sink


def run_query_loop(
    session: "Session",
    prompt: str,
    *,
    sink: EventSink | None = None,
) -> str:
    from ..session import Session  # local import to avoid cycle

    if not isinstance(session, Session):
        raise TypeError("session must be a Session")

    sink = sink or null_sink
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

    try:
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
                response, streamed_text = _create_message_with_retries(
                    session,
                    sink,
                    turn_user_prompt=planning_prompt if planning_prompt_pending else None,
                )
            planning_prompt_pending = False
            assistant_message: Message = {"role": "assistant", "content": response.content}
            session.state.messages.append(assistant_message)
            _enforce_message_budget(session, sink)

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
                    write_constraints_active = True
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

            with _turn_execution_scope(session, write_constraints_active=write_constraints_active):
                tool_result_blocks = session.execute_tool_calls(
                    response.tool_calls,
                    session.tool_context(),
                    sink=sink,
                )
            session.state.messages.append({"role": "user", "content": tool_result_blocks})
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
                    kind="assistant_tool_result_ready",
                    message=f"received {len(tool_result_blocks)} tool result block(s); continuing assistant response",
                )
            )
    except Exception:
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
        raise
    finally:
        session.clear_plan_execution()
        session.clear_execution_constraint()

    raise RuntimeError("Max turn count reached.")


def _create_message_with_retries(
    session: "Session",
    sink: EventSink,
    *,
    turn_user_prompt: str | None = None,
):
    return _create_provider_message_with_retries(
        session.provider,
        session=session,
        messages=_messages_for_provider_call(session, turn_user_prompt=turn_user_prompt),
        tools=session.tool_specs(),
        system_prompt=session.build_system_prompt(),
        sink=sink,
        allow_streaming=not session.has_advisor_model(),
    )


def _create_provider_message_with_retries(
    provider,
    *,
    session: "Session",
    messages: list[dict],
    tools: list[dict],
    system_prompt: str,
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
    return _create_provider_message_once(
        session.provider,
        messages=_messages_for_provider_call(session),
        tools=session.tool_specs(),
        system_prompt=session.build_system_prompt(),
        sink=sink,
        allow_streaming=not session.has_advisor_model(),
    )


def _create_provider_message_once(
    provider,
    *,
    messages: list[dict],
    tools: list[dict],
    system_prompt: str,
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
        for event in provider.stream_message(
            messages=messages,
            tools=tools,
            system_prompt=system_prompt,
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
        provider.create_message(
            messages=messages,
            tools=tools,
            system_prompt=system_prompt,
        ),
        False,
    )


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
    )


def _enforce_message_budget(session: "Session", sink: EventSink) -> None:
    count = len(session.state.messages)
    if count <= session.config.max_history_messages:
        return
    _compact_history(session, sink)
    count = len(session.state.messages)
    if count > session.config.max_history_messages:
        raise RuntimeError(
            "Message budget exceeded after compaction: "
            f"{count} messages > {session.config.max_history_messages}"
        )


def _compact_history(session: "Session", sink: EventSink) -> None:
    keep_last = max(
        1,
        min(session.config.history_keep_last_messages, session.config.max_history_messages),
    )
    messages = session.state.messages
    if len(messages) <= keep_last:
        return

    compacted_messages = messages[:-keep_last]
    session.state.messages = messages[-keep_last:]

    compacted_lines = []
    for index, message in enumerate(compacted_messages, start=1):
        compacted_lines.append(
            f"- {index}. {message.get('role', 'unknown')}: {session._summarize_message(message)}"
        )
    new_summary = "Earlier conversation summary:\n" + "\n".join(compacted_lines)

    existing_summary = session.state.context_summary or ""
    merged_summary = f"{existing_summary}\n\n{new_summary}".strip() if existing_summary else new_summary
    max_chars = session.config.max_context_summary_chars
    if len(merged_summary) > max_chars:
        kept_tail = max_chars - len("[older compacted context truncated]\n")
        merged_summary = "[older compacted context truncated]\n" + merged_summary[-kept_tail:]
    session.state.context_summary = merged_summary
    sink(
        RuntimeEvent(
            kind="context_compacted",
            message=(
                f"compacted {len(compacted_messages)} messages into context_summary; "
                f"kept last {len(session.state.messages)} messages"
            ),
        )
    )
