from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import TYPE_CHECKING, Any
import json

from .tools import McpToolAdapter

if TYPE_CHECKING:
    from .session import Session


@dataclass(slots=True, frozen=True)
class ContextUsageCategory:
    name: str
    tokens: int
    details: str = ""


@dataclass(slots=True, frozen=True)
class ContextUsageReport:
    model: str
    max_tokens: int
    total_tokens: int
    percentage: float
    categories: tuple[ContextUsageCategory, ...]


def estimate_text_tokens(text: str) -> int:
    raw = str(text or "")
    if not raw:
        return 0
    return max(1, ceil(len(raw) / 4))


def collect_context_usage(
    session: "Session",
    *,
    message_override: list[dict[str, Any]] | None = None,
    replacement_aware_provider_view: bool = False,
) -> ContextUsageReport:
    categories: list[ContextUsageCategory] = []
    auto_enabled_skills, manually_enabled_skills = session._runtime_context.active_skills_by_source(session.state)
    for block in session.system_prompt_blocks():
        category_name = _system_prompt_block_category_name(block.text)
        details = ""
        if category_name == "Project memory":
            details = _path_details(session.project_context.memory_path)
        elif category_name == "Auto-enabled skills":
            details = _skills_details(auto_enabled_skills)
        elif category_name == "Manually enabled skills":
            details = _skills_details(manually_enabled_skills)
        elif category_name == "Planning artifact":
            artifact = session.active_planning_artifact()
            if artifact is not None:
                details = f"{artifact.kind}: {artifact.goal}"
        _append_category(categories, category_name, [block.text], details=details)

    source_messages = session.state.messages if message_override is None else message_override
    message_pieces = [_serialize_message(message) for message in source_messages]
    _append_category(
        categories,
        "Conversation messages",
        [piece for piece in message_pieces if piece],
        details=(
            f"{len(source_messages)} message(s)"
            + (" replacement-aware provider view" if replacement_aware_provider_view else "")
        ),
    )

    active_tools = list(session._available_tools())
    active_specs = {spec["name"]: spec for spec in session.tool_specs()}
    default_tool_specs: list[str] = []
    mcp_tool_specs: list[str] = []
    for tool in active_tools:
        spec = active_specs.get(tool.name) or tool.to_model_tool()
        rendered = json.dumps(spec, ensure_ascii=True, sort_keys=True)
        if isinstance(tool, McpToolAdapter):
            mcp_tool_specs.append(rendered)
        else:
            default_tool_specs.append(rendered)
    _append_category(
        categories,
        "Default tools",
        default_tool_specs,
        details=f"{len(default_tool_specs)} tool(s)",
    )
    _append_category(
        categories,
        "MCP tools",
        mcp_tool_specs,
        details=f"{len(mcp_tool_specs)} tool(s)",
    )

    total_tokens = sum(category.tokens for category in categories)
    max_tokens = max(int(session.config.max_tokens), 0)
    percentage = (total_tokens / max_tokens * 100.0) if max_tokens else 0.0
    return ContextUsageReport(
        model=session.config.model,
        max_tokens=max_tokens,
        total_tokens=total_tokens,
        percentage=percentage,
        categories=tuple(categories),
    )


def render_context_usage(
    report: ContextUsageReport,
    *,
    system_prompt_surface: dict[str, Any] | None = None,
    replacement_surface: dict[str, Any] | None = None,
    prompt_prefix_surface: dict[str, Any] | None = None,
) -> str:
    lines = [
        "## Context Usage",
        "",
        f"model: {report.model}",
        f"estimated tokens: {report.total_tokens} / {report.max_tokens}",
        f"percentage: {report.percentage:.1f}%",
    ]
    if system_prompt_surface is not None:
        boundary = system_prompt_surface.get("system_prompt_dynamic_boundary_index")
        lines.extend(
            [
                f"system prompt blocks: {system_prompt_surface.get('system_prompt_block_count', 0)}",
                "dynamic boundary: " + (str(boundary) if boundary is not None else "none"),
                f"static prompt chars: {system_prompt_surface.get('system_prompt_prefix_chars', 0)}",
                f"dynamic prompt chars: {system_prompt_surface.get('system_prompt_dynamic_chars', 0)}",
            ]
        )
    if replacement_surface is not None:
        lines.extend(
            [
                "tool-result artifacts active: "
                + str(replacement_surface.get("artifact_active_count", 0)),
                "tool-result replacements active: "
                + str(replacement_surface.get("replacement_active_count", 0)),
                "replacement-aware provider view: yes",
            ]
        )
    if prompt_prefix_surface is not None:
        lines.extend(
            [
                "prompt prefix: "
                + (
                    f"segments={prompt_prefix_surface.get('prompt_prefix_segment_count', 0)} "
                    f"stable_chars={prompt_prefix_surface.get('prompt_prefix_stable_chars', 0)} "
                    f"dynamic_tail_chars={prompt_prefix_surface.get('prompt_prefix_dynamic_tail_chars', 0)}"
                ),
                "provider-view assembly: "
                + str(prompt_prefix_surface.get("prompt_prefix_provider_view_summary") or "none"),
                "plan attachments: "
                + str(prompt_prefix_surface.get("prompt_prefix_attachment_summary") or "none"),
                "plan attachment mode: "
                + str(prompt_prefix_surface.get("prompt_prefix_attachment_mode") or "none"),
                "plan workflow: "
                + (
                    f"{prompt_prefix_surface.get('plan_workflow_mode') or 'five_phase'} "
                    f"agents={prompt_prefix_surface.get('plan_workflow_agent_count') or 1} "
                    f"explore_agents={prompt_prefix_surface.get('plan_workflow_explore_agent_count') or 3}"
                ),
                "prompt prefix cache mode: "
                + str(prompt_prefix_surface.get("prompt_prefix_cache_mode") or "disabled"),
                "prompt prefix cache supported: "
                + ("yes" if bool(prompt_prefix_surface.get("prompt_prefix_cache_supported")) else "no"),
                "prompt prefix cache provider: "
                + str(prompt_prefix_surface.get("prompt_prefix_cache_provider") or "none"),
                "prompt prefix cache summary: "
                + str(prompt_prefix_surface.get("prompt_prefix_cache_summary") or "none"),
                "prompt prefix cache fallback reason: "
                + str(prompt_prefix_surface.get("prompt_prefix_cache_fallback_reason") or "none"),
                "provider-view planner: "
                + str(prompt_prefix_surface.get("prompt_prefix_planner_mode") or "disabled"),
                "prefix reduction tier: "
                + str(prompt_prefix_surface.get("prompt_prefix_reduction_tier") or "none"),
                "planner reason: "
                + str(prompt_prefix_surface.get("prompt_prefix_planner_reason") or "none"),
                "costed planner mode: "
                + str(prompt_prefix_surface.get("prompt_prefix_costed_planner_mode") or "disabled"),
                "costed planner reason: "
                + str(prompt_prefix_surface.get("prompt_prefix_costed_planner_reason") or "none"),
                "target tokens to shed: "
                + str(prompt_prefix_surface.get("prompt_prefix_target_tokens_to_shed") or 0),
                "selected candidates: "
                + str(prompt_prefix_surface.get("prompt_prefix_selected_candidate_summary") or "none"),
                "remaining estimated overage: "
                + str(prompt_prefix_surface.get("prompt_prefix_remaining_estimated_overage") or 0),
                "provider-view orchestration: "
                + str(prompt_prefix_surface.get("prompt_prefix_orchestration_mode") or "disabled"),
                "orchestration reason: "
                + str(prompt_prefix_surface.get("prompt_prefix_orchestration_reason") or "none"),
                "orchestration selected candidates: "
                + str(
                    prompt_prefix_surface.get(
                        "prompt_prefix_orchestration_selected_candidate_summary"
                    )
                    or "none"
                ),
                "orchestration remaining overage: "
                + str(
                    prompt_prefix_surface.get(
                        "prompt_prefix_orchestration_remaining_estimated_overage"
                    )
                    or 0
                ),
                "full compaction required: "
                + (
                    "yes"
                    if bool(
                        prompt_prefix_surface.get(
                            "prompt_prefix_orchestration_requires_full_compaction"
                        )
                    )
                    else "no"
                ),
                "preserved prefix signature: "
                + str(prompt_prefix_surface.get("prompt_prefix_preserved_signature") or "none"),
                "preserved message groups: "
                + str(prompt_prefix_surface.get("prompt_prefix_preserved_message_group_count") or 0),
                "prefix signature: "
                + str(prompt_prefix_surface.get("prompt_prefix_signature") or "none"),
                "prefix preserved: "
                + ("no" if bool(prompt_prefix_surface.get("prompt_prefix_changed")) else "yes"),
                "prefix change reason: "
                + str(prompt_prefix_surface.get("prompt_prefix_change_reason") or "none"),
                "plan attachment change reason: "
                + str(
                    prompt_prefix_surface.get("prompt_prefix_attachment_change_reason")
                    or "none"
                ),
            ]
        )
    lines.extend(
        [
            "",
            "estimated usage by category:",
            "",
            "| Category | Estimated Tokens | Percentage | Details |",
            "|---|---:|---:|---|",
        ]
    )
    if report.categories:
        for category in report.categories:
            percent = (category.tokens / report.max_tokens * 100.0) if report.max_tokens else 0.0
            details = category.details or "-"
            lines.append(
                f"| {category.name} | {category.tokens} | {percent:.1f}% | {details} |"
            )
    else:
        lines.append("| None | 0 | 0.0% | - |")
    return "\n".join(lines)


def render_compaction_policy(policy: dict[str, Any]) -> str:
    state = str(policy.get("compaction_state") or "ok")
    lines = [
        "automatic compaction policy:",
        f"state: {state}",
        "would compact now: " + ("yes" if bool(policy.get("would_compact")) else "no"),
    ]
    reason = str(policy.get("compaction_reason") or "").strip()
    if reason:
        lines.append(f"reason: {reason}")
    runtime_budget_lines = policy.get("runtime_budget_narrative_lines")
    if isinstance(runtime_budget_lines, list) and runtime_budget_lines:
        lines.extend(str(item) for item in runtime_budget_lines if str(item).strip())
    else:
        lines.append("runtime budget state: " + str(policy.get("runtime_budget_state") or "ok"))
        runtime_reason = str(policy.get("runtime_budget_reason") or "").strip()
        lines.append(f"runtime budget reason: {runtime_reason or 'none'}")
        lines.append("context token source: " + str(policy.get("context_token_source") or "none"))
        last_turn_token_count = policy.get("last_turn_token_count")
        lines.append(
            "last turn token count: "
            + (str(last_turn_token_count) if last_turn_token_count is not None else "none")
        )
        lines.append("last turn token source: " + str(policy.get("last_turn_token_source") or "none"))
        lines.append(
            "provider usage seen: "
            + ("yes" if bool(policy.get("provider_usage_seen")) else "no")
        )
        lines.append("budget pressure: " + str(policy.get("budget_pressure") or "ok"))
    prompt_prefix_lines = policy.get("prompt_prefix_narrative_lines")
    if isinstance(prompt_prefix_lines, list) and prompt_prefix_lines:
        lines.extend(str(item) for item in prompt_prefix_lines if str(item).strip())
    if policy.get("compact_preview_action"):
        lines.append(f"preview action: {policy['compact_preview_action']}")
    if policy.get("compact_apply_action"):
        lines.append(f"apply action: {policy['compact_apply_action']}")
    if "tool_result_replacements" in policy:
        lines.append(
            "tool-result replacements: " + str(policy.get("tool_result_replacements") or 0)
        )
    if "tool_result_artifacts" in policy:
        lines.append("tool-result artifacts: " + str(policy.get("tool_result_artifacts") or 0))
    if "replacement_aware_compaction" in policy:
        lines.append(
            "replacement-aware compaction: "
            + str(policy.get("replacement_aware_compaction") or "no")
        )
    return "\n".join(lines)


def _append_category(
    categories: list[ContextUsageCategory],
    name: str,
    pieces: list[str],
    *,
    details: str = "",
) -> None:
    rendered = [piece for piece in pieces if piece]
    if not rendered:
        return
    tokens = sum(estimate_text_tokens(piece) for piece in rendered)
    if tokens <= 0:
        return
    categories.append(ContextUsageCategory(name=name, tokens=tokens, details=details))


def _system_prompt_block_category_name(text: str) -> str:
    if text.startswith("Project memory:\n"):
        return "Project memory"
    if text.startswith("Auto-enabled project skills:\n"):
        return "Auto-enabled skills"
    if text.startswith("Manually enabled project skills:\n"):
        return "Manually enabled skills"
    if text.startswith("Compacted conversation context from earlier turns:\n"):
        return "Compacted context summary"
    if text.startswith("Recent planning artifact to reuse when relevant:\n"):
        return "Planning artifact"
    if text.startswith("Current session checklist:\n"):
        return "Session checklist"
    if text.startswith("Session checklist guidance:\n"):
        return "Session checklist guidance"
    return "Base instructions"


def _render_skills_block(title: str, skills: list[Any]) -> str:
    lines: list[str] = []
    for skill in skills:
        source = getattr(skill, "source", "project-local")
        source_owner = getattr(skill, "source_owner", "workspace")
        if source == "plugin":
            source_label = f"plugin:{source_owner}"
        else:
            source_label = str(source)
        lines.append(f"- {skill.name} ({skill.path.name}, {source_label})")
        if skill.content:
            lines.append(skill.content)
    return title + ":\n" + "\n".join(lines)


def _skills_details(skills: list[Any]) -> str:
    project_local = sum(1 for skill in skills if getattr(skill, "source", "project-local") == "project-local")
    plugin = sum(1 for skill in skills if getattr(skill, "source", "") == "plugin")
    builtin = sum(1 for skill in skills if getattr(skill, "source", "") == "builtin")
    return (
        f"{len(skills)} skill(s); "
        f"project_local={project_local} plugin={plugin} builtin={builtin}"
    )


def _serialize_message(message: dict[str, Any]) -> str:
    role = str(message.get("role") or "unknown")
    content = message.get("content")
    if not isinstance(content, list):
        rendered = json.dumps(message, ensure_ascii=True, sort_keys=True)
        return f"role={role}\n{rendered}"
    blocks: list[str] = []
    for index, block in enumerate(content, start=1):
        blocks.append(_serialize_content_block(role=role, block=block, index=index))
    blocks = [block for block in blocks if block]
    if not blocks:
        return f"role={role}"
    return "\n".join([f"role={role}", *blocks])


def _serialize_content_block(*, role: str, block: Any, index: int) -> str:
    if not isinstance(block, dict):
        return f"block_{index}: {json.dumps(block, ensure_ascii=True, sort_keys=True)}"
    block_type = str(block.get("type") or "unknown")
    if block_type == "text":
        text = str(block.get("text") or "")
        return f"block_{index}: type=text\n{text}"
    if block_type == "tool_use":
        tool_name = str(block.get("name") or "")
        tool_input = json.dumps(block.get("input") or {}, ensure_ascii=True, sort_keys=True)
        return (
            f"block_{index}: type=tool_use role={role} name={tool_name}\n"
            f"input={tool_input}"
        )
    if block_type == "tool_result":
        content = str(block.get("content") or "")
        tool_use_id = str(block.get("tool_use_id") or "")
        is_error = "yes" if block.get("is_error") else "no"
        return (
            f"block_{index}: type=tool_result role={role} tool_use_id={tool_use_id} is_error={is_error}\n"
            f"{content}"
        )
    return f"block_{index}: {json.dumps(block, ensure_ascii=True, sort_keys=True)}"


def _path_details(path: Any) -> str:
    if path is None:
        return ""
    return str(path)
