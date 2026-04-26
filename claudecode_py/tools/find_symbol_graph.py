from __future__ import annotations

from collections import defaultdict

from .base import BaseTool, resolve_workspace_path
from .find_references import FindReferencesTool


class FindSymbolGraphTool(BaseTool):
    name = "find_symbol_graph"
    description = "Summarize a symbol's definitions and likely references across the project, grouped by file."
    read_only = True
    concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Symbol name to summarize.",
            },
            "path": {
                "type": "string",
                "description": "Optional file or subdirectory to scope the graph.",
            },
            "scope": {
                "type": "string",
                "description": "Reference search scope: auto, current_file, current_dir, or workspace.",
                "enum": ["auto", "current_file", "current_dir", "workspace"],
            },
            "max_definitions": {
                "type": "integer",
                "description": "Maximum number of definitions to include.",
            },
            "max_references": {
                "type": "integer",
                "description": "Maximum number of references to include.",
            },
        },
        "required": ["symbol"],
    }

    def execute(self, tool_input: dict, ctx):
        symbol = tool_input["symbol"]
        base = resolve_workspace_path(ctx.cwd, tool_input.get("path", "."))
        path_filter = None if base == ctx.cwd else base.relative_to(ctx.cwd).as_posix()
        max_definitions = int(tool_input.get("max_definitions", 20))
        max_references = int(tool_input.get("max_references", 40))
        scope = tool_input.get("scope", "auto")

        definitions = [
            entry.render()
            for entry in ctx.session.get_python_symbol_index().find(
                symbol,
                path_filter=path_filter,
                max_results=max_definitions,
            )
        ]
        if len(definitions) < max_definitions:
            definitions.extend(
                entry.render()
                for entry in ctx.session.get_js_ts_symbol_index().find(
                    symbol,
                    path_filter=path_filter,
                    max_results=max_definitions - len(definitions),
                )
            )
        references = self._collect_references(
            symbol=symbol,
            path=tool_input.get("path", "."),
            scope=scope,
            max_results=max_references,
            ctx=ctx,
        )
        import_graph = self._collect_import_graph(definitions, ctx)
        inheritance_graph = self._collect_inheritance_graph(symbol=symbol, definitions=definitions, ctx=ctx)
        call_graph = self._collect_call_graph(symbol=symbol, definitions=definitions, ctx=ctx)

        if not definitions and not references and not import_graph and not inheritance_graph and not call_graph:
            return f'No definitions or references found for "{symbol}".'

        parts = [
            f"Symbol graph for {symbol}",
            f"scope: {tool_input.get('path', '.')} ({scope})",
            "summary:",
            f"- definitions={len(definitions)}",
            f"- reference_files={self._count_reference_files(references)}",
            f"- reference_edges={len(references)}",
            f"- import_nodes={self._count_graph_nodes(import_graph)}",
            f"- inheritance_nodes={self._count_graph_nodes(inheritance_graph)}",
            f"- call_nodes={self._count_graph_nodes(call_graph)}",
        ]
        if definitions:
            parts.append("definitions:")
            parts.extend(f"- {item}" for item in definitions)
        else:
            parts.append("definitions:\n- none")

        if references:
            parts.append("references:")
            grouped: dict[str, list[str]] = defaultdict(list)
            for item in references:
                rel_path, _, rest = item.partition(":")
                grouped[rel_path].append(rest)
            for rel_path in sorted(grouped):
                parts.append(f"- {rel_path}")
                for ref in grouped[rel_path]:
                    parts.append(f"  - {ref}")
        else:
            parts.append("references:\n- none")

        if import_graph:
            parts.append("imports:")
            parts.extend(import_graph)
        else:
            parts.append("imports:\n- none")
        if inheritance_graph:
            parts.append("inheritance:")
            parts.extend(inheritance_graph)
        else:
            parts.append("inheritance:\n- none")
        if call_graph:
            parts.append("calls:")
            parts.extend(call_graph)
        else:
            parts.append("calls:\n- none")
        return "\n".join(parts)

    def _collect_references(
        self,
        *,
        symbol: str,
        path: str,
        scope: str,
        max_results: int,
        ctx,
    ) -> list[str]:
        rendered = FindReferencesTool().execute(
            {
                "symbol": symbol,
                "path": path,
                "scope": scope,
                "max_results": max_results,
            },
            ctx,
        )
        if rendered == "No references found.":
            return []
        return [line for line in rendered.splitlines() if line.strip()]

    def _collect_import_graph(self, definitions: list[str], ctx) -> list[str]:
        python_index = ctx.session.get_python_symbol_index()
        js_ts_index = ctx.session.get_js_ts_symbol_index()
        definition_paths = sorted({item.split(":", maxsplit=1)[0] for item in definitions})
        if not definition_paths:
            return []
        lines: list[str] = []
        for rel_path in definition_paths:
            imports = []
            importers = []
            if rel_path.endswith(".py"):
                imports = python_index.imports_for_path(rel_path)
                module_name = self._python_module_name_from_path(rel_path)
                importers = python_index.importers_for_module(module_name)
            elif self._is_js_ts_path(rel_path):
                imports = js_ts_index.imports_for_path(rel_path)
                module_name = self._js_ts_module_id_from_path(rel_path)
                importers = js_ts_index.importers_for_module(module_name)
            if not imports and not importers:
                continue
            lines.append(f"- {rel_path}")
            if imports:
                unique_imports = list(dict.fromkeys(item.module for item in imports))
                preview = ", ".join(unique_imports[:4])
                if len(unique_imports) > 4:
                    preview += f", ... ({len(unique_imports) - 4} more)"
                lines.append(f"  - imports: {preview}")
            if importers:
                unique_importers = sorted({item.rel_path for item in importers})
                importer_preview = ", ".join(unique_importers[:4])
                if len(unique_importers) > 4:
                    importer_preview += f", ... ({len(unique_importers) - 4} more)"
                lines.append(f"  - imported_by: {importer_preview}")
        return lines

    def _python_module_name_from_path(self, rel_path: str) -> str:
        if rel_path.endswith("/__init__.py"):
            return rel_path[: -len("/__init__.py")].replace("/", ".")
        if rel_path.endswith(".py"):
            return rel_path[:-3].replace("/", ".")
        return rel_path.replace("/", ".")

    def _js_ts_module_id_from_path(self, rel_path: str) -> str:
        without_suffix = rel_path
        for suffix in (".tsx", ".jsx", ".mjs", ".cjs", ".ts", ".js"):
            if without_suffix.endswith(suffix):
                without_suffix = without_suffix[: -len(suffix)]
                break
        if without_suffix.endswith("/index"):
            return without_suffix[: -len("/index")]
        return without_suffix

    def _is_js_ts_path(self, rel_path: str) -> bool:
        return rel_path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"))

    def _collect_inheritance_graph(self, *, symbol: str, definitions: list[str], ctx) -> list[str]:
        python_index = ctx.session.get_python_symbol_index()
        class_definitions = [
            item for item in definitions if item.endswith(f":class {symbol}") or f":class {symbol}" in item
        ]
        if not class_definitions:
            return []
        lines: list[str] = []
        seen_paths: set[str] = set()
        for entry in python_index.inheritance_for_class(symbol):
            seen_paths.add(entry.rel_path)
            lines.append(f"- {entry.rel_path}")
            if entry.bases:
                lines.append(f"  - bases: {', '.join(entry.bases)}")
            else:
                lines.append("  - bases: (none)")
        derived = python_index.derived_classes(symbol)
        if derived:
            if not lines:
                lines.append(f"- {symbol}")
            derived_preview = ", ".join(
                f"{item.class_name} ({item.rel_path}:{item.line})" for item in derived[:5]
            )
            if len(derived) > 5:
                derived_preview += f", ... ({len(derived) - 5} more)"
            lines.append(f"  - derived_by: {derived_preview}")
        return lines

    def _collect_call_graph(self, *, symbol: str, definitions: list[str], ctx) -> list[str]:
        python_index = ctx.session.get_python_symbol_index()
        js_ts_index = ctx.session.get_js_ts_symbol_index()
        callers = python_index.calls_for_callee(symbol)
        js_ts_callers = js_ts_index.calls_for_callee(symbol)
        caller_defs: list[tuple[str, str | None, str]] = []
        for item in definitions:
            parts = item.split(":", maxsplit=2)
            if len(parts) != 3:
                continue
            descriptor = parts[2]
            caller_name, caller_owner = self._parse_callable_descriptor(descriptor)
            if caller_name == symbol:
                caller_defs.append((caller_name, caller_owner, parts[0]))
        outgoing = []
        js_ts_outgoing = []
        for caller_name, caller_owner, rel_path in caller_defs:
            if rel_path.endswith(".py"):
                outgoing.extend(python_index.calls_from_caller(caller_name, caller_owner=caller_owner))
            elif self._is_js_ts_path(rel_path):
                js_ts_outgoing.extend(js_ts_index.calls_from_caller(caller_name, caller_owner=caller_owner))
        if not callers and not js_ts_callers and not outgoing and not js_ts_outgoing:
            return []
        lines: list[str] = []
        if callers or js_ts_callers:
            all_callers = [*callers, *js_ts_callers]
            preview = ", ".join(
                f"{item.caller_owner + '.' if item.caller_owner else ''}{item.caller_name} ({item.rel_path}:{item.line})"
                for item in all_callers[:6]
            )
            if len(all_callers) > 6:
                preview += f", ... ({len(all_callers) - 6} more)"
            lines.append(f"- called_by: {preview}")
        if outgoing or js_ts_outgoing:
            all_outgoing = [*outgoing, *js_ts_outgoing]
            unique_outgoing = []
            seen = set()
            for item in all_outgoing:
                key = (item.callee_name, item.rel_path, item.line)
                if key in seen:
                    continue
                seen.add(key)
                unique_outgoing.append(item)
            preview = ", ".join(
                f"{item.callee_name} ({item.rel_path}:{item.line})"
                for item in unique_outgoing[:6]
            )
            if len(unique_outgoing) > 6:
                preview += f", ... ({len(unique_outgoing) - 6} more)"
            lines.append(f"- calls: {preview}")
        return lines

    def _parse_callable_descriptor(self, descriptor: str) -> tuple[str | None, str | None]:
        if descriptor.startswith("class "):
            return None, None
        if ".def " in descriptor:
            owner, name = descriptor.split(".def ", maxsplit=1)
            return name, owner
        if ".method " in descriptor:
            owner, name = descriptor.split(".method ", maxsplit=1)
            return name, owner
        if descriptor.startswith("def "):
            return descriptor[4:], None
        if descriptor.startswith("function "):
            return descriptor[9:], None
        if descriptor.startswith("const => "):
            return descriptor[9:], None
        if descriptor.startswith("const function "):
            return descriptor[15:], None
        return None, None

    def _count_reference_files(self, references: list[str]) -> int:
        return len({item.partition(":")[0] for item in references})

    def _count_graph_nodes(self, lines: list[str]) -> int:
        return sum(1 for line in lines if line.startswith("- "))
