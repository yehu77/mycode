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


def collect_context_usage(session: "Session") -> ContextUsageReport:
    categories: list[ContextUsageCategory] = []

    _append_category(
        categories,
        "Base instructions",
        [session.base_system_prompt],
    )

    if session.project_context.memory_content:
        _append_category(
            categories,
            "Project memory",
            ["Project memory:\n" + session.project_context.memory_content],
            details=_path_details(session.project_context.memory_path),
        )

    auto_enabled_skills, manually_enabled_skills = session._runtime_context.active_skills_by_source(session.state)
    if auto_enabled_skills:
        _append_category(
            categories,
            "Auto-enabled skills",
            [_render_skills_block("Auto-enabled project skills", auto_enabled_skills)],
            details=f"{len(auto_enabled_skills)} skill(s)",
        )
    if manually_enabled_skills:
        _append_category(
            categories,
            "Manually enabled skills",
            [_render_skills_block("Manually enabled project skills", manually_enabled_skills)],
            details=f"{len(manually_enabled_skills)} skill(s)",
        )

    if session.state.context_summary:
        _append_category(
            categories,
            "Compacted context summary",
            ["Compacted conversation context from earlier turns:\n" + session.state.context_summary],
        )

    artifact = session.active_planning_artifact()
    if artifact is not None:
        planning_context = (
            f"artifact_id: {artifact.artifact_id}\n"
            f"kind: {artifact.kind}\n"
            f"goal: {artifact.goal}\n"
            f"summary:\n{artifact.summary}"
        )
        _append_category(
            categories,
            "Planning artifact",
            ["Recent planning artifact to reuse when relevant:\n" + planning_context],
            details=f"{artifact.kind}: {artifact.goal}",
        )

    message_pieces = [_serialize_message(message) for message in session.state.messages]
    _append_category(
        categories,
        "Conversation messages",
        [piece for piece in message_pieces if piece],
        details=f"{len(session.state.messages)} message(s)",
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


def render_context_usage(report: ContextUsageReport) -> str:
    lines = [
        "## Context Usage",
        "",
        f"model: {report.model}",
        f"estimated tokens: {report.total_tokens} / {report.max_tokens}",
        f"percentage: {report.percentage:.1f}%",
        "",
        "estimated usage by category:",
        "",
        "| Category | Estimated Tokens | Percentage | Details |",
        "|---|---:|---:|---|",
    ]
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


def _render_skills_block(title: str, skills: list[Any]) -> str:
    lines: list[str] = []
    for skill in skills:
        lines.append(f"- {skill.name} ({skill.path.name})")
        if skill.content:
            lines.append(skill.content)
    return title + ":\n" + "\n".join(lines)


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
