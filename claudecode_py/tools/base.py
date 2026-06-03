from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path
from typing import Any, TYPE_CHECKING
import json

if TYPE_CHECKING:
    from ..permissions import PermissionManager
    from ..tasks import TaskManager
    from ..session import Session

from ..permissions import ApprovalRequest
from ..state import WorkspaceFileChange


def resolve_workspace_path(cwd: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = cwd / path
    path = path.resolve()
    try:
        path.relative_to(cwd)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {path}") from exc
    return path


def format_tool_output(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=True, indent=2)


@dataclass(slots=True)
class ToolContext:
    cwd: Path
    permission_manager: "PermissionManager"
    task_manager: "TaskManager"
    session: "Session"


@dataclass(slots=True)
class FileApprovalChange:
    path: str
    kind: str
    before: str | None
    after: str | None
    metadata_lines: tuple[str, ...] = ()
    preview_path: str | None = None
    preview_note: str | None = None


def workspace_change_action_kind(change: WorkspaceFileChange) -> str:
    if change.action_kind:
        return change.action_kind
    if not change.existed_before and change.after_content is not None:
        return "create"
    if change.existed_before and change.after_content is None:
        return "delete"
    return "update"


def is_visible_workspace_change(change: WorkspaceFileChange) -> bool:
    return workspace_change_action_kind(change) != "move_source"


def workspace_change_metadata_lines(change: WorkspaceFileChange) -> tuple[str, ...]:
    lines: list[str] = []
    action_kind = workspace_change_action_kind(change)
    if action_kind == "move" and change.source_path:
        lines.append(f"from: {change.source_path}")
    if change.change_mode:
        lines.append(f"mode: {change.change_mode}")
    if change.replacement_count is not None:
        lines.append(f"replacements: {change.replacement_count}")
    return tuple(lines)


def workspace_change_to_approval(change: WorkspaceFileChange) -> FileApprovalChange:
    return FileApprovalChange(
        path=change.path,
        kind=workspace_change_action_kind(change),
        before=change.before_content,
        after=change.after_content,
        metadata_lines=workspace_change_metadata_lines(change),
    )


def count_workspace_change_actions(changes: list[WorkspaceFileChange]) -> dict[str, int]:
    counts = {"create": 0, "update": 0, "delete": 0, "move": 0}
    for change in changes:
        action_kind = workspace_change_action_kind(change)
        if action_kind == "move_source":
            continue
        if action_kind not in counts:
            counts["update"] += 1
            continue
        counts[action_kind] += 1
    return counts


def describe_workspace_change(change: WorkspaceFileChange) -> str:
    action_kind = workspace_change_action_kind(change)
    if action_kind == "create":
        return f"created {change.path}"
    if action_kind == "delete":
        return f"deleted {change.path}"
    if action_kind == "move" and change.source_path:
        return f"moved {change.source_path} -> {change.path}"
    return f"updated {change.path}"


class BaseTool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = True
    concurrency_safe: bool = False
    risk_level: str | None = None
    deferred: bool = False
    search_terms: tuple[str, ...] = ()

    def to_model_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def approval_request(
        self,
        tool_input: dict[str, Any],
        ctx: ToolContext | None = None,
    ) -> ApprovalRequest:
        risk_level = self.declared_risk_level()
        return ApprovalRequest(
            tool_name=self.name,
            reason=self.description,
            risk_level=risk_level,
            approval_key=risk_level,
        )

    def is_deferred(self) -> bool:
        return self.deferred

    def schema_source(self) -> str:
        return "builtin"

    def matches_search_query(self, query: str) -> bool:
        normalized = query.casefold().strip()
        if not normalized:
            return False
        haystacks = [self.name.casefold(), self.description.casefold()]
        haystacks.extend(term.casefold() for term in self.search_terms)
        tokens = [token for token in normalized.split() if token]
        if not tokens:
            return False
        return all(any(token in haystack for haystack in haystacks) for token in tokens)

    def declared_risk_level(self) -> str:
        if self.risk_level is not None:
            return self.risk_level
        return "read" if self.read_only else "write"

    @abstractmethod
    def execute(self, tool_input: dict[str, Any], ctx: ToolContext) -> Any:
        raise NotImplementedError


def render_diff_preview(
    rel_path: str,
    before: str,
    after: str,
    *,
    max_lines: int = 16,
    include_path_header: bool = True,
) -> str:
    if before == after:
        body = "(no content changes)"
        if include_path_header:
            return f"path={rel_path}\n{body}"
        return body
    diff_lines = list(
        unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
            lineterm="",
            n=2,
        )
    )
    rendered = diff_lines[:max_lines]
    if len(diff_lines) > max_lines:
        rendered.append("... diff truncated ...")
    body = "\n".join(rendered)
    if include_path_header:
        return f"path={rel_path}\n{body}"
    return body


def render_change_summary(
    rel_path: str,
    before: str,
    after: str | None,
    *,
    max_preview_lines: int = 4,
) -> str:
    if after is None:
        action = "deleted"
        diff_lines = list(
            unified_diff(
                before.splitlines(),
                [],
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
                lineterm="",
                n=1,
            )
        )
    elif before == "" and after != "":
        action = "created"
        diff_lines = list(
            unified_diff(
                [],
                after.splitlines(),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
                lineterm="",
                n=1,
            )
        )
    elif before == after:
        return f"{rel_path} (no content changes)"
    else:
        action = "updated"
        diff_lines = list(
            unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
                lineterm="",
                n=1,
            )
        )

    additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removals = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    header = f"{action} {rel_path} (+{additions} -{removals})"

    preview_lines = [
        line
        for line in diff_lines
        if line.startswith(("@@", "+", "-")) and not line.startswith(("+++", "---"))
    ][:max_preview_lines]
    if not preview_lines:
        return header
    if len(
        [
            line
            for line in diff_lines
            if line.startswith(("@@", "+", "-")) and not line.startswith(("+++", "---"))
        ]
    ) > max_preview_lines:
        preview_lines.append("... diff truncated ...")
    return header + "\n" + "\n".join(preview_lines)


def render_change_detail(
    rel_path: str,
    before: str,
    after: str | None,
    *,
    max_lines: int = 14,
) -> str:
    if after is None:
        diff_lines = list(
            unified_diff(
                before.splitlines(),
                [],
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
                lineterm="",
                n=2,
            )
        )
    elif before == "" and after != "":
        diff_lines = list(
            unified_diff(
                [],
                after.splitlines(),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
                lineterm="",
                n=2,
            )
        )
    elif before == after:
        return f"path={rel_path}\n(no content changes)"
    else:
        diff_lines = list(
            unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
                lineterm="",
                n=2,
            )
        )

    rendered = diff_lines[:max_lines]
    if len(diff_lines) > max_lines:
        rendered.append("... diff truncated ...")
    return "\n".join(rendered) if rendered else f"path={rel_path}\n(no content changes)"


def truncate_detail_lines(text: str, *, max_lines: int = 18, max_width: int = 160) -> str:
    lines = text.splitlines()
    rendered: list[str] = []
    for line in lines[:max_lines]:
        if len(line) > max_width:
            rendered.append(line[: max_width - 3] + "...")
        else:
            rendered.append(line)
    if len(lines) > max_lines:
        rendered.append("... preview truncated ...")
    return "\n".join(rendered)


def render_pending_preview(
    title: str,
    *,
    summary_lines: list[str] | None = None,
    sections: list[tuple[str, str]] | None = None,
    max_lines: int = 24,
    max_width: int = 160,
) -> str:
    lines = [title]
    if summary_lines:
        lines.extend(summary_lines)
    if sections:
        for label, body in sections:
            if lines:
                lines.append("")
            lines.append(f"[{label}]")
            lines.extend(body.splitlines() or ["(empty)"])
    return truncate_detail_lines("\n".join(lines), max_lines=max_lines, max_width=max_width)


def render_file_change_preview(
    changes: list[FileApprovalChange],
    *,
    title: str = "Pending file changes",
    max_lines: int = 24,
    max_width: int = 160,
) -> str:
    summary_lines = [f"files: {len(changes)}"]
    counts: dict[str, int] = {}
    for change in changes:
        counts[change.kind] = counts.get(change.kind, 0) + 1
    for kind in ("create", "update", "delete", "move"):
        if counts.get(kind):
            summary_lines.append(f"{kind}: {counts[kind]}")

    sections: list[tuple[str, str]] = []
    for change in changes:
        display_path = change.preview_path or change.path
        body_lines = [f"action: {change.kind}"]
        body_lines.extend(change.metadata_lines)
        if change.preview_note:
            body_lines.append(f"preview: {change.preview_note}")
        else:
            before = change.before or ""
            after = "" if change.after is None else change.after
            diff_body = render_diff_preview(
                display_path,
                before,
                after,
                include_path_header=False,
            )
            body_lines.extend(diff_body.splitlines() or ["(empty)"])
        sections.append((f"file {display_path}", "\n".join(body_lines)))

    return render_pending_preview(
        title,
        summary_lines=summary_lines,
        sections=sections,
        max_lines=max_lines,
        max_width=max_width,
    )
