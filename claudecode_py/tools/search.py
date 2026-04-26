from __future__ import annotations

from pathlib import Path
import fnmatch
import re
import shutil
import subprocess

from .base import BaseTool, resolve_workspace_path


class GlobTool(BaseTool):
    name = "glob_search"
    description = "Find files in the workspace using a glob pattern."
    read_only = True
    concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern like **/*.py"},
            "path": {"type": "string", "description": "Optional subdirectory to search from."},
            "max_results": {"type": "integer", "description": "Maximum number of results."},
        },
        "required": ["pattern"],
    }

    def execute(self, tool_input: dict, ctx):
        base = resolve_workspace_path(ctx.cwd, tool_input.get("path", "."))
        pattern = tool_input["pattern"]
        max_results = int(tool_input.get("max_results", 200))
        matches: list[str] = []
        for path in base.rglob("*"):
            rel = path.relative_to(ctx.cwd).as_posix()
            if fnmatch.fnmatch(rel, pattern):
                matches.append(rel)
                if len(matches) >= max_results:
                    break
        return "\n".join(matches) if matches else "No files matched."


class GrepTool(BaseTool):
    name = "grep_search"
    description = "Search file contents with a regex pattern."
    read_only = True
    concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for."},
            "path": {"type": "string", "description": "Optional subdirectory to search from."},
            "max_results": {"type": "integer", "description": "Maximum number of matches."},
        },
        "required": ["pattern"],
    }

    def execute(self, tool_input: dict, ctx):
        base = resolve_workspace_path(ctx.cwd, tool_input.get("path", "."))
        pattern = tool_input["pattern"]
        max_results = int(tool_input.get("max_results", 100))
        rg = shutil.which("rg")
        if rg:
            completed = subprocess.run(
                [rg, "--line-number", "--color", "never", "--glob", "!.git", pattern, str(base)],
                capture_output=True,
                text=True,
                check=False,
            )
            output = completed.stdout.strip()
            if not output:
                return "No matches found."
            lines = output.splitlines()[:max_results]
            return "\n".join(lines)

        regex = re.compile(pattern)
        matches: list[str] = []
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            for index, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    rel = path.relative_to(ctx.cwd).as_posix()
                    matches.append(f"{rel}:{index}:{line}")
                    if len(matches) >= max_results:
                        return "\n".join(matches)
        return "\n".join(matches) if matches else "No matches found."
