from __future__ import annotations

from .base import BaseTool, resolve_workspace_path


class FindCalleesTool(BaseTool):
    name = "find_callees"
    description = "Find project call-graph callees for a function or method symbol across Python and JS/TS."
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
                "description": "Maximum number of callee edges to return.",
            },
        },
        "required": ["symbol"],
    }

    def execute(self, tool_input: dict, ctx):
        symbol = tool_input["symbol"]
        max_results = int(tool_input.get("max_results", 50))
        base = resolve_workspace_path(ctx.cwd, tool_input.get("path", "."))
        path_filter = None if base == ctx.cwd else base.relative_to(ctx.cwd).as_posix()

        python_index = ctx.session.get_python_symbol_index()
        js_ts_index = ctx.session.get_js_ts_symbol_index()
        definitions = [
            ("python", entry)
            for entry in python_index.find(symbol, path_filter=path_filter, max_results=max_results)
            if entry.kind in {"def", "async def"}
        ]
        if len(definitions) < max_results:
            definitions.extend(
                ("js_ts", entry)
                for entry in js_ts_index.find(
                    symbol,
                    path_filter=path_filter,
                    max_results=max_results - len(definitions),
                )
                if entry.kind in {"function", "method", "const =>", "const function"}
            )
        if not definitions:
            return f'No function or method definitions found for "{symbol}" in the project indexes.'

        parts = [f"Callees for {symbol}"]
        total_edges = 0
        for source, definition in definitions:
            if source == "python":
                edges = python_index.calls_from_caller(
                    definition.name,
                    caller_owner=definition.owner,
                    path_filter=path_filter,
                )
            else:
                edges = js_ts_index.calls_from_caller(
                    definition.name,
                    caller_owner=definition.owner,
                    path_filter=path_filter,
                )
            header = definition.render()
            parts.append(f"- {header}")
            if not edges:
                parts.append("  - no callees")
                continue
            shown = 0
            seen: set[tuple[str, int, str]] = set()
            for edge in edges:
                key = (edge.rel_path, edge.line, edge.callee_name)
                if key in seen:
                    continue
                seen.add(key)
                parts.append(f"  - {edge.line}:{edge.callee_name}")
                shown += 1
                total_edges += 1
                if total_edges >= max_results:
                    break
            if total_edges >= max_results:
                break
            if len(edges) > shown:
                parts.append(f"  - ... ({len(edges) - shown} more)")
        return "\n".join(parts)
