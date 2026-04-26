from __future__ import annotations

from .base import BaseTool, resolve_workspace_path


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read a file from the workspace."
    read_only = True
    concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to the workspace."},
            "start_line": {"type": "integer", "description": "Optional 1-based start line."},
            "end_line": {"type": "integer", "description": "Optional 1-based end line."},
        },
        "required": ["path"],
    }

    def execute(self, tool_input: dict, ctx):
        path = resolve_workspace_path(ctx.cwd, tool_input["path"])
        text = path.read_text(encoding="utf-8")
        start_line = tool_input.get("start_line")
        end_line = tool_input.get("end_line")
        if start_line or end_line:
            lines = text.splitlines()
            start = max((start_line or 1) - 1, 0)
            end = end_line or len(lines)
            sliced = lines[start:end]
            numbered = [f"{idx + start + 1:>4}: {line}" for idx, line in enumerate(sliced)]
            return "\n".join(numbered)
        return text
