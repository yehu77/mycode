from __future__ import annotations

import ast
import re

from .base import BaseTool, resolve_workspace_path

_INDEXED_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}


class FindSymbolTool(BaseTool):
    name = "find_symbol"
    description = "Find symbol definitions by name, with strongest support for Python classes, functions, async functions, and methods."
    read_only = True
    concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Symbol name to find.",
            },
            "path": {
                "type": "string",
                "description": "Optional file or subdirectory to search from.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of definitions to return.",
            },
        },
        "required": ["symbol"],
    }

    def execute(self, tool_input: dict, ctx):
        symbol = tool_input["symbol"]
        path = tool_input.get("path", ".")
        max_results = int(tool_input.get("max_results", 50))
        base = resolve_workspace_path(ctx.cwd, path)
        indexed_matches = [
            self._render_symbol_location(item)
            for item in ctx.session.locate_symbol(symbol, path=path, max_results=max_results).matches
        ]

        if base.is_file():
            generic_matches = []
            if base.suffix.lower() not in _INDEXED_SUFFIXES:
                generic_matches = self._collect_matches(base, ctx.cwd, symbol)
            matches = [*indexed_matches, *generic_matches]
            if not matches:
                return "No symbol definitions found."
            return "\n".join(matches[:max_results])

        matches: list[str] = []
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() in _INDEXED_SUFFIXES:
                continue
            matches.extend(self._collect_matches(path, ctx.cwd, symbol))
            if len(matches) >= max_results:
                break
        combined = [*indexed_matches, *matches]
        return "\n".join(combined[:max_results]) if combined else "No symbol definitions found."

    def _render_symbol_location(self, location) -> str:
        if location.owner:
            return f"{location.path}:{location.line}:{location.owner}.{location.kind} {location.symbol}"
        return f"{location.path}:{location.line}:{location.kind} {location.symbol}"

    def _collect_matches(self, path, cwd, symbol: str) -> list[str]:
        suffix = path.suffix.lower()
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            return []
        rel = path.relative_to(cwd).as_posix()
        if suffix == ".py":
            return self._collect_python_matches(text, rel, symbol)
        return self._collect_generic_matches(text, rel, symbol)

    def _collect_python_matches(self, text: str, rel_path: str, symbol: str) -> list[str]:
        try:
            module = ast.parse(text)
        except SyntaxError:
            return []

        matches: list[str] = []
        for node in module.body:
            if isinstance(node, ast.ClassDef):
                if node.name == symbol:
                    matches.append(f"{rel_path}:{node.lineno}:class {node.name}")
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == symbol:
                        owner = node.name
                        kind = "async def" if isinstance(item, ast.AsyncFunctionDef) else "def"
                        matches.append(f"{rel_path}:{item.lineno}:{owner}.{kind} {item.name}")
                continue
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
                kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                matches.append(f"{rel_path}:{node.lineno}:{kind} {node.name}")
        return matches

    def _collect_generic_matches(self, text: str, rel_path: str, symbol: str) -> list[str]:
        escaped = re.escape(symbol)
        patterns = [
            re.compile(rf"^\s*class\s+{escaped}\b"),
            re.compile(rf"^\s*def\s+{escaped}\b"),
            re.compile(rf"^\s*async\s+def\s+{escaped}\b"),
            re.compile(rf"^\s*function\s+{escaped}\b"),
            re.compile(rf"^\s*export\s+(?:class|function|const)\s+{escaped}\b"),
            re.compile(rf"^\s*const\s+{escaped}\b"),
        ]
        matches: list[str] = []
        for index, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if any(pattern.search(stripped) for pattern in patterns):
                matches.append(f"{rel_path}:{index}:{stripped}")
        return matches
