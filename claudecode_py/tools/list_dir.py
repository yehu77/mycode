from __future__ import annotations

from .base import BaseTool, resolve_workspace_path


class ListDirTool(BaseTool):
    name = "list_dir"
    description = "List files and directories under a workspace directory."
    read_only = True
    concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path relative to the workspace. Use '.' for workspace root.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of entries to return.",
            },
        },
        "required": [],
    }

    def execute(self, tool_input: dict, ctx):
        path = resolve_workspace_path(ctx.cwd, tool_input.get("path", "."))
        if not path.exists():
            raise FileNotFoundError(f"Directory does not exist: {path}")
        if not path.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {path}")

        max_results = int(tool_input.get("max_results", 200))
        entries = sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        lines: list[str] = []
        for entry in entries[:max_results]:
            rel = entry.relative_to(ctx.cwd).as_posix()
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{rel}{suffix}")

        if len(entries) > max_results:
            lines.append(f"... ({len(entries) - max_results} more)")

        return "\n".join(lines) if lines else "(empty directory)"
