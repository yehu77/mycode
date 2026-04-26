from __future__ import annotations

from collections import defaultdict

from .base import BaseTool, resolve_workspace_path


class OutlineProjectTool(BaseTool):
    name = "outline_project"
    description = "Summarize project-level Python and JS/TS structure using the workspace symbol indexes, grouped by file."
    read_only = True
    concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Optional subdirectory or file path to scope the project outline.",
            },
            "max_files": {
                "type": "integer",
                "description": "Maximum number of files to include.",
            },
            "max_symbols_per_file": {
                "type": "integer",
                "description": "Maximum number of symbols to list per file.",
            },
        },
    }

    def execute(self, tool_input: dict, ctx):
        base = resolve_workspace_path(ctx.cwd, tool_input.get("path", "."))
        path_filter = None if base == ctx.cwd else base.relative_to(ctx.cwd).as_posix()
        max_files = int(tool_input.get("max_files", 20))
        max_symbols_per_file = int(tool_input.get("max_symbols_per_file", 8))

        grouped: dict[str, list[str]] = defaultdict(list)
        python_index = ctx.session.get_python_symbol_index()
        js_ts_index = ctx.session.get_js_ts_symbol_index()
        total_symbols = 0
        for entry in [*python_index.entries, *js_ts_index.entries]:
            if path_filter is not None:
                normalized = path_filter.rstrip("/")
                if entry.rel_path != normalized and not entry.rel_path.startswith(normalized + "/"):
                    continue
            grouped[entry.rel_path].append(entry.render())
            total_symbols += 1

        if not grouped:
            target = tool_input.get("path", ".")
            return (
                f"Project outline for {target}\n"
                "No indexed project symbols found in this scope."
            )

        parts = [
            f"Project outline for {tool_input.get('path', '.')}",
            f"indexed_files: {len(grouped)}",
            f"indexed_symbols: {total_symbols}",
            f"indexed_python_files: {python_index.indexed_files}",
            f"indexed_js_ts_files: {js_ts_index.indexed_files}",
            f"indexed_python_symbols: {len(python_index.entries)}",
            f"indexed_js_ts_symbols: {len(js_ts_index.entries)}",
            f"indexed_python_imports: {len(python_index.imports)}",
            f"indexed_js_ts_imports: {len(js_ts_index.imports)}",
            f"indexed_python_inheritances: {len(python_index.inheritances)}",
            f"indexed_python_calls: {len(python_index.calls)}",
            f"indexed_js_ts_calls: {len(js_ts_index.calls)}",
            "files:",
        ]
        for rel_path in sorted(grouped)[:max_files]:
            parts.append(f"- {rel_path}")
            for symbol_line in grouped[rel_path][:max_symbols_per_file]:
                _, _, symbol_text = symbol_line.partition(":")
                _, _, symbol_text = symbol_text.partition(":")
                parts.append(f"  - {symbol_text}")
            imports = python_index.imports_for_path(rel_path)
            js_ts_imports = js_ts_index.imports_for_path(rel_path)
            if imports:
                unique_modules = []
                seen_modules = set()
                for item in imports:
                    if item.module in seen_modules:
                        continue
                    seen_modules.add(item.module)
                    unique_modules.append(item.module)
                preview = ", ".join(unique_modules[:3])
                if len(unique_modules) > 3:
                    preview += f", ... ({len(unique_modules) - 3} more)"
                parts.append(f"  - imports: {preview}")
            elif js_ts_imports:
                unique_modules = []
                seen_modules = set()
                for item in js_ts_imports:
                    if item.module in seen_modules:
                        continue
                    seen_modules.add(item.module)
                    unique_modules.append(item.module)
                preview = ", ".join(unique_modules[:3])
                if len(unique_modules) > 3:
                    preview += f", ... ({len(unique_modules) - 3} more)"
                parts.append(f"  - imports: {preview}")
            inheritances = [item for item in python_index.inheritances if item.rel_path == rel_path and item.bases]
            if inheritances:
                preview = ", ".join(
                    f"{item.class_name} <- {', '.join(item.bases)}" for item in inheritances[:2]
                )
                if len(inheritances) > 2:
                    preview += f", ... ({len(inheritances) - 2} more)"
                parts.append(f"  - inherits: {preview}")
            calls = [item for item in python_index.calls if item.rel_path == rel_path]
            js_ts_calls = [item for item in js_ts_index.calls if item.rel_path == rel_path]
            if calls:
                unique_callees = sorted({item.callee_name for item in calls})
                preview = ", ".join(unique_callees[:4])
                if len(unique_callees) > 4:
                    preview += f", ... ({len(unique_callees) - 4} more)"
                parts.append(f"  - calls: {preview}")
            elif js_ts_calls:
                unique_callees = sorted({item.callee_name for item in js_ts_calls})
                preview = ", ".join(unique_callees[:4])
                if len(unique_callees) > 4:
                    preview += f", ... ({len(unique_callees) - 4} more)"
                parts.append(f"  - calls: {preview}")
            hidden = len(grouped[rel_path]) - max_symbols_per_file
            if hidden > 0:
                parts.append(f"  - ... ({hidden} more)")
        hidden_files = len(grouped) - max_files
        if hidden_files > 0:
            parts.append(f"... ({hidden_files} more files)")
        return "\n".join(parts)
