from __future__ import annotations

from ..permissions import ApprovalRequest
from ..state import WorkspaceFileChange
from .base import BaseTool, render_file_change_preview, resolve_workspace_path, workspace_change_to_approval


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Create or overwrite a file with complete content."
    read_only = False
    concurrency_safe = False
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to the workspace.",
            },
            "content": {
                "type": "string",
                "description": "Full file content to write.",
            },
            "make_parents": {
                "type": "boolean",
                "description": "Create parent directories if needed.",
            },
        },
        "required": ["path", "content"],
    }

    def approval_request(self, tool_input: dict, ctx=None) -> ApprovalRequest:
        request = super().approval_request(tool_input, ctx)
        if ctx is None:
            return request
        path = resolve_workspace_path(ctx.cwd, tool_input["path"])
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        after = tool_input["content"]
        rel_path = path.relative_to(ctx.cwd).as_posix()
        change = WorkspaceFileChange(
            path=rel_path,
            existed_before=path.exists(),
            before_content=before,
            after_content=after,
            action_kind="create" if not path.exists() else "update",
            change_mode="" if not path.exists() else "overwrite content",
        )
        request.target_paths = (rel_path,)
        request.details = render_file_change_preview(
            [workspace_change_to_approval(change)],
            max_lines=20,
        )
        return request

    def execute(self, tool_input: dict, ctx):
        validator = getattr(ctx.session, "validate_plan_mode_tool_policy", None)
        if validator is not None:
            validator(self.name, tool_input)
        path = resolve_workspace_path(ctx.cwd, tool_input["path"])
        make_parents = bool(tool_input.get("make_parents", True))
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        elif not path.parent.exists():
            raise FileNotFoundError(f"Parent directory does not exist: {path.parent}")

        existed = path.exists()
        before = path.read_text(encoding="utf-8") if existed else ""
        after = tool_input["content"]
        path.write_text(tool_input["content"], encoding="utf-8")
        rel_path = path.relative_to(ctx.cwd).as_posix()
        change = WorkspaceFileChange(
            path=rel_path,
            existed_before=existed,
            before_content=before,
            after_content=after,
            action_kind="create" if not existed else "update",
            change_mode="" if not existed else "overwrite content",
        )
        ctx.session.record_workspace_change(
            tool_name=self.name,
            summary=f"{'Created' if not existed else 'Updated'} {rel_path}",
            file_changes=[change],
        )
        return f"{'Created' if not existed else 'Updated'} {rel_path}"
