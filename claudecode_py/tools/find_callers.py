from __future__ import annotations

from collections import defaultdict

from .base import BaseTool, resolve_workspace_path


class FindCallersTool(BaseTool):
    name = "find_callers"
    description = "Find project call-graph callers for a symbol across Python and JS/TS, grouped by file."
    read_only = True
    concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Function or method name to inspect.",
            },
            "path": {
                "type": "string",
                "description": "Optional file or subdirectory scope.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of caller edges to return.",
            },
        },
        "required": ["symbol"],
    }

    def execute(self, tool_input: dict, ctx):
        symbol = tool_input["symbol"]
        max_results = int(tool_input.get("max_results", 50))
        base = resolve_workspace_path(ctx.cwd, tool_input.get("path", "."))
        path_filter = None if base == ctx.cwd else base.relative_to(ctx.cwd).as_posix()

        python_callers = ctx.session.get_python_symbol_index().calls_for_callee(
            symbol,
            path_filter=path_filter,
        )
        js_ts_callers = ctx.session.get_js_ts_symbol_index().calls_for_callee(
            symbol,
            path_filter=path_filter,
        )
        callers = [*python_callers, *js_ts_callers]
        if not callers:
            return f'No callers found for "{symbol}" in the project call graph.'

        grouped: dict[str, list[str]] = defaultdict(list)
        for entry in callers[:max_results]:
            caller = f"{entry.caller_owner}.{entry.caller_name}" if entry.caller_owner else entry.caller_name
            grouped[entry.rel_path].append(f"{entry.line}:{caller} -> {entry.callee_name}")

        parts = [f"Callers for {symbol}"]
        for rel_path in sorted(grouped):
            parts.append(f"- {rel_path}")
            for item in grouped[rel_path]:
                parts.append(f"  - {item}")
        hidden = len(callers) - max_results
        if hidden > 0:
            parts.append(f"... ({hidden} more caller edge(s))")
        return "\n".join(parts)
