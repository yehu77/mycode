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


class BaseTool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = True
    concurrency_safe: bool = False
    risk_level: str | None = None

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
) -> str:
    if before == after:
        return f"path={rel_path}\n(no content changes)"
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
    return f"path={rel_path}\n" + "\n".join(rendered)


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
