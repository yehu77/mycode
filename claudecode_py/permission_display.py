from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class PermissionDisplayContext:
    decision_reason: str = ""
    permission_rules: tuple[str, ...] = ()
    command_mode_name: str = ""
    command_mode_source: str = ""
    command_mode_allowed_prefixes: tuple[str, ...] = ()
    command_mode_violating_segment: str = ""
    command_mode_violating_segment_index: int | None = None
    command_mode_complex_features: tuple[str, ...] = ()


def has_permission_display_context(context: PermissionDisplayContext) -> bool:
    return bool(
        context.decision_reason
        or context.permission_rules
        or context.command_mode_name
    )


def format_violating_segment(segment: str, index: int | None) -> str:
    if index is None:
        return segment
    return f"segment {index}: {segment}"


def render_command_mode_lines(
    context: PermissionDisplayContext,
    *,
    bullet_prefix: str = "- ",
) -> list[str]:
    lines = [f"{bullet_prefix}mode: {context.command_mode_name}"]
    if context.command_mode_source:
        lines.append(f"{bullet_prefix}source: {context.command_mode_source}")
    if context.command_mode_allowed_prefixes:
        lines.append(
            f"{bullet_prefix}allowed_prefixes: "
            + ", ".join(context.command_mode_allowed_prefixes)
        )
    if context.command_mode_violating_segment:
        lines.append(
            f"{bullet_prefix}violating_segment: "
            + format_violating_segment(
                context.command_mode_violating_segment,
                context.command_mode_violating_segment_index,
            )
        )
    if context.command_mode_complex_features:
        lines.append(
            f"{bullet_prefix}complex_features: "
            + ", ".join(context.command_mode_complex_features)
        )
    return lines


def render_permission_display_lines(
    context: PermissionDisplayContext,
    *,
    policy_label: str = "",
    matched_rules_header: str = "matched_rules:",
    command_mode_header: str = "",
    bullet_prefix: str = "- ",
    nested_bullet_prefix: str = "- ",
) -> list[str]:
    lines: list[str] = []
    if context.decision_reason and policy_label:
        lines.append(f"{policy_label}: {context.decision_reason}")
    if context.permission_rules:
        lines.append(matched_rules_header)
        lines.extend(f"{bullet_prefix}{rule}" for rule in context.permission_rules[:6])
    if context.command_mode_name and command_mode_header:
        lines.append(command_mode_header)
        lines.extend(
            render_command_mode_lines(
                context,
                bullet_prefix=nested_bullet_prefix,
            )
        )
    return lines


def render_permission_display_compact(context: PermissionDisplayContext) -> str:
    if not has_permission_display_context(context):
        return ""
    parts: list[str] = []
    if context.decision_reason:
        parts.append(f"policy={context.decision_reason}")
    if context.command_mode_name:
        parts.append(f"mode={context.command_mode_name}")
    if context.command_mode_violating_segment:
        parts.append(
            "segment="
            + format_violating_segment(
                context.command_mode_violating_segment,
                context.command_mode_violating_segment_index,
            )
        )
    if context.command_mode_complex_features:
        parts.append("complex_feature=" + ",".join(context.command_mode_complex_features))
    return " | ".join(parts)


def permission_display_context_to_dict(context: PermissionDisplayContext) -> dict[str, Any]:
    return {
        "decision_reason": context.decision_reason,
        "permission_rules": list(context.permission_rules),
        "command_mode_name": context.command_mode_name,
        "command_mode_source": context.command_mode_source,
        "command_mode_allowed_prefixes": list(context.command_mode_allowed_prefixes),
        "command_mode_violating_segment": context.command_mode_violating_segment,
        "command_mode_violating_segment_index": context.command_mode_violating_segment_index,
        "command_mode_complex_features": list(context.command_mode_complex_features),
        "display_lines": render_permission_display_lines(
            context,
            policy_label="policy",
            command_mode_header="command_mode:",
            bullet_prefix="- ",
            nested_bullet_prefix="- ",
        ),
        "display_compact": render_permission_display_compact(context),
    }


def render_approval_request_lines(
    request: Any,
    *,
    include_title: bool = False,
    footer_lines: tuple[str, ...] = (),
) -> list[str]:
    context = PermissionDisplayContext(
        decision_reason=str(getattr(request, "decision_reason", "") or ""),
        permission_rules=tuple(getattr(request, "permission_rules", ()) or ()),
        command_mode_name=str(getattr(request, "command_mode_name", "") or ""),
        command_mode_source=str(getattr(request, "command_mode_source", "") or ""),
        command_mode_allowed_prefixes=tuple(getattr(request, "command_mode_allowed_prefixes", ()) or ()),
        command_mode_violating_segment=str(getattr(request, "command_mode_violating_segment", "") or ""),
        command_mode_violating_segment_index=getattr(request, "command_mode_violating_segment_index", None),
        command_mode_complex_features=tuple(getattr(request, "command_mode_complex_features", ()) or ()),
    )
    lines: list[str] = []
    if include_title:
        lines.append("Approval")
    lines.extend(
        [
            f"risk: {getattr(request, 'risk_level', '')}",
            f"tool: {getattr(request, 'tool_name', '')}",
            f"reason: {getattr(request, 'reason', '')}",
        ]
    )
    lines.extend(
        render_permission_display_lines(
            context,
            policy_label="policy",
            command_mode_header="command_mode:",
            bullet_prefix="- ",
            nested_bullet_prefix="- ",
        )
    )
    target_paths = tuple(getattr(request, "target_paths", ()) or ())
    if target_paths:
        lines.append("paths:")
        lines.extend(f"- {path}" for path in target_paths[:6])
    command = str(getattr(request, "command", "") or "").strip()
    if command:
        lines.append(f"command: {command}")
    details = str(getattr(request, "details", "") or "").strip()
    if details:
        if str(getattr(request, "tool_name", "") or "") == "workspace_cleanup":
            lines.append("details:")
            detail_lines = [line for line in details.splitlines() if line.strip()]
            for line in detail_lines[:8]:
                lines.append(f"- {line}")
            remaining = len(detail_lines) - 8
            if remaining > 0:
                lines.append(f"- ... {remaining} more detail lines")
        else:
            lines.append("")
            lines.append("Preview mirrored in Changes panel.")
    if footer_lines:
        lines.append("")
        lines.extend(footer_lines)
    return lines


def render_approval_request_compact(request: Any) -> str:
    context = PermissionDisplayContext(
        decision_reason=str(getattr(request, "decision_reason", "") or ""),
        permission_rules=tuple(getattr(request, "permission_rules", ()) or ()),
        command_mode_name=str(getattr(request, "command_mode_name", "") or ""),
        command_mode_source=str(getattr(request, "command_mode_source", "") or ""),
        command_mode_allowed_prefixes=tuple(getattr(request, "command_mode_allowed_prefixes", ()) or ()),
        command_mode_violating_segment=str(getattr(request, "command_mode_violating_segment", "") or ""),
        command_mode_violating_segment_index=getattr(request, "command_mode_violating_segment_index", None),
        command_mode_complex_features=tuple(getattr(request, "command_mode_complex_features", ()) or ()),
    )
    parts = [
        f"risk={getattr(request, 'risk_level', '')}",
        f"tool={getattr(request, 'tool_name', '')}",
    ]
    permission_compact = render_permission_display_compact(context)
    if permission_compact:
        parts.append(permission_compact)
    return " | ".join(part for part in parts if part)
