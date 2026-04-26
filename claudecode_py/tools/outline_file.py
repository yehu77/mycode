from __future__ import annotations

import ast

from .base import BaseTool, resolve_workspace_path


class OutlineFileTool(BaseTool):
    name = "outline_file"
    description = "Summarize the structure of a source file, especially Python classes, methods, functions, and top-level constants."
    read_only = True
    concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to the workspace.",
            }
        },
        "required": ["path"],
    }

    def execute(self, tool_input: dict, ctx):
        path = resolve_workspace_path(ctx.cwd, tool_input["path"])
        text = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()
        if suffix == ".py":
            return self._outline_python(text, tool_input["path"])
        return self._outline_text(text, tool_input["path"])

    def _outline_python(self, text: str, display_path: str) -> str:
        module = ast.parse(text)

        classes: list[str] = []
        functions: list[str] = []
        constants: list[str] = []

        for node in module.body:
            if isinstance(node, ast.ClassDef):
                classes.append(self._format_class(node))
                continue
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(self._format_function(node))
                continue
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        constants.append(f"- {target.id} (line {node.lineno})")
                continue
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id.isupper():
                    constants.append(f"- {node.target.id} (line {node.lineno})")

        parts = [f"Outline for {display_path}", "language: python"]
        if classes:
            parts.append("classes:")
            parts.extend(classes)
        if functions:
            parts.append("functions:")
            parts.extend(functions)
        if constants:
            parts.append("constants:")
            parts.extend(constants)
        if len(parts) == 2:
            parts.append("No classes, functions, or top-level constants found.")
        return "\n".join(parts)

    def _format_class(self, node: ast.ClassDef) -> str:
        method_lines: list[str] = []
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "async def" if isinstance(item, ast.AsyncFunctionDef) else "def"
                method_lines.append(f"  - {kind} {item.name}() (line {item.lineno})")
        if method_lines:
            return "\n".join(
                [f"- class {node.name} (line {node.lineno})", *method_lines]
            )
        return f"- class {node.name} (line {node.lineno})"

    def _format_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        return f"- {kind} {node.name}() (line {node.lineno})"

    def _outline_text(self, text: str, display_path: str) -> str:
        interesting: list[str] = []
        for index, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(("class ", "def ", "async def ", "function ", "export ", "const ")):
                interesting.append(f"- line {index}: {stripped}")
            if len(interesting) >= 40:
                break

        if not interesting:
            return (
                f"Outline for {display_path}\n"
                "language: generic\n"
                "No structural outline found. Use read_file for full contents."
            )
        return "\n".join(
            [
                f"Outline for {display_path}",
                "language: generic",
                "interesting lines:",
                *interesting,
            ]
        )
