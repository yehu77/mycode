from __future__ import annotations

import ast
import re
from pathlib import Path

from .base import BaseTool, resolve_workspace_path


class FindReferencesTool(BaseTool):
    name = "find_references"
    description = "Find likely references to a symbol, using AST-aware analysis for Python and explicit search scopes when needed."
    read_only = True
    concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Symbol name to search for.",
            },
            "path": {
                "type": "string",
                "description": "Optional file or subdirectory to search from.",
            },
            "scope": {
                "type": "string",
                "description": "Search scope: auto, current_file, current_dir, or workspace.",
                "enum": ["auto", "current_file", "current_dir", "workspace"],
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of references to return.",
            },
        },
        "required": ["symbol"],
    }

    def execute(self, tool_input: dict, ctx):
        symbol = tool_input["symbol"]
        scope = tool_input.get("scope", "auto")
        base = self._resolve_search_base(ctx.cwd, tool_input.get("path"), scope)
        max_results = int(tool_input.get("max_results", 100))
        search_paths = self._iter_search_paths(base)
        python_method_owners = self._collect_python_method_owners(base, ctx, search_paths, symbol)

        if base.is_file():
            matches = self._collect_matches(base, ctx.cwd, symbol, python_method_owners)
            return "\n".join(matches[:max_results]) if matches else "No references found."

        matches: list[str] = []
        for path in search_paths:
            matches.extend(self._collect_matches(path, ctx.cwd, symbol, python_method_owners))
            if len(matches) >= max_results:
                break
        return "\n".join(matches[:max_results]) if matches else "No references found."

    def _resolve_search_base(self, cwd: Path, path_value: str | None, scope: str) -> Path:
        if scope == "workspace":
            return cwd
        if scope == "current_file":
            if not path_value:
                raise ValueError('scope "current_file" requires a file path.')
            base = resolve_workspace_path(cwd, path_value)
            if not base.is_file():
                raise ValueError(f'scope "current_file" requires a file path, got: {base}')
            return base
        if scope == "current_dir":
            base = resolve_workspace_path(cwd, path_value or ".")
            return base.parent if base.is_file() else base
        if scope != "auto":
            raise ValueError(f"Unsupported scope: {scope}")
        return resolve_workspace_path(cwd, path_value or ".")

    def _iter_search_paths(self, base: Path) -> list[Path]:
        if base.is_file():
            return [base]
        return [path for path in sorted(base.rglob("*")) if path.is_file()]

    def _collect_python_method_owners(self, base: Path, ctx, paths: list[Path], symbol: str) -> set[str]:
        path_filter = None if base == ctx.cwd else base.relative_to(ctx.cwd).as_posix()
        owners = ctx.session.get_python_symbol_index().method_owner_classes(
            symbol,
            path_filter=path_filter,
        )
        if owners:
            return owners
        return self._collect_python_method_owners_fallback(paths, symbol)

    def _collect_python_method_owners_fallback(self, paths: list[Path], symbol: str) -> set[str]:
        owners: set[str] = set()
        for path in paths:
            if path.suffix.lower() != ".py":
                continue
            try:
                text = path.read_text(encoding="utf-8")
                module = ast.parse(text)
            except Exception:  # noqa: BLE001
                continue
            for node in module.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                if any(
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == symbol
                    for item in node.body
                ):
                    owners.add(node.name)
        return owners

    def _collect_matches(self, path: Path, cwd: Path, symbol: str, python_method_owners: set[str]) -> list[str]:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            return []
        rel = path.relative_to(cwd).as_posix()
        suffix = path.suffix.lower()
        if suffix == ".py":
            return self._collect_python_matches(text, rel, symbol, python_method_owners)
        if suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
            return self._collect_js_ts_matches(text, rel, symbol)
        return self._collect_generic_matches(text, rel, symbol)

    def _collect_python_matches(
        self,
        text: str,
        rel_path: str,
        symbol: str,
        python_method_owners: set[str],
    ) -> list[str]:
        try:
            module = ast.parse(text)
        except SyntaxError:
            return self._collect_generic_matches(text, rel_path, symbol)

        collector = _PythonReferenceCollector(symbol, python_method_owners)
        collector.visit(module)

        lines = text.splitlines()
        matches: list[str] = []
        for line_no in sorted(collector.reference_lines):
            if line_no < 1 or line_no > len(lines):
                continue
            content = lines[line_no - 1].strip()
            if not content:
                continue
            matches.append(f"{rel_path}:{line_no}:{content}")
        return matches

    def _collect_generic_matches(self, text: str, rel_path: str, symbol: str) -> list[str]:
        regex = re.compile(rf"\b{re.escape(symbol)}\b")
        matches: list[str] = []
        for index, line in enumerate(text.splitlines(), start=1):
            if not regex.search(line):
                continue
            if self._looks_like_definition(line.strip(), symbol):
                continue
            matches.append(f"{rel_path}:{index}:{line.strip()}")
        return matches

    def _collect_js_ts_matches(self, text: str, rel_path: str, symbol: str) -> list[str]:
        regex = re.compile(rf"\b{re.escape(symbol)}\b")
        matches: list[str] = []
        for index, line in enumerate(text.splitlines(), start=1):
            if not regex.search(line):
                continue
            stripped = line.strip()
            if not stripped or self._looks_like_definition(stripped, symbol):
                continue
            matches.append(f"{rel_path}:{index}:{stripped}")
        return matches

    def _looks_like_definition(self, stripped_line: str, symbol: str) -> bool:
        definition_patterns = [
            rf"^\s*class\s+{re.escape(symbol)}\b",
            rf"^\s*def\s+{re.escape(symbol)}\b",
            rf"^\s*async\s+def\s+{re.escape(symbol)}\b",
            rf"^\s*function\s+{re.escape(symbol)}\b",
            rf"^\s*export\s+(?:class|function|const)\s+{re.escape(symbol)}\b",
            rf"^\s*const\s+{re.escape(symbol)}\b",
        ]
        return any(re.search(pattern, stripped_line) for pattern in definition_patterns)


class _PythonReferenceCollector(ast.NodeVisitor):
    def __init__(self, symbol: str, method_owner_classes: set[str]) -> None:
        self.symbol = symbol
        self.method_owner_classes = method_owner_classes
        self.reference_lines: set[int] = set()
        self.current_class: str | None = None
        self.scope_stack: list[dict[str, set[str]]] = [{}]
        self.self_attr_types_by_class: dict[str, dict[str, set[str]]] = {}

    def visit_Module(self, node: ast.Module) -> None:
        for item in node.body:
            self.visit(item)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        previous_class = self.current_class
        self.current_class = node.name
        for item in node.body:
            self.visit(item)
        self.current_class = previous_class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        scope: dict[str, set[str]] = {}
        if self.current_class and node.args.args:
            first_arg = node.args.args[0].arg
            if first_arg in {"self", "cls"}:
                scope[first_arg] = {self.current_class}
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            inferred_types = self._infer_annotation_types(arg.annotation)
            if inferred_types:
                scope[arg.arg] = inferred_types
        if node.args.vararg is not None:
            inferred_types = self._infer_annotation_types(node.args.vararg.annotation)
            if inferred_types:
                scope[node.args.vararg.arg] = inferred_types
        if node.args.kwarg is not None:
            inferred_types = self._infer_annotation_types(node.args.kwarg.annotation)
            if inferred_types:
                scope[node.args.kwarg.arg] = inferred_types
        self.scope_stack.append(scope)
        for item in node.body:
            self.visit(item)
        self.scope_stack.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        inferred_types = self._infer_expr_types(node.value)
        for target in node.targets:
            self._record_assignment_target(target, inferred_types)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        annotation_types = self._infer_annotation_types(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            inferred_types = annotation_types | self._infer_expr_types(node.value)
            self._record_assignment_target(node.target, inferred_types)
            return
        self._record_assignment_target(node.target, annotation_types)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        self.visit(node.target)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == self.symbol and isinstance(node.ctx, ast.Load):
            self.reference_lines.add(node.lineno)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == self.symbol and self._attribute_matches_target(node):
            self.reference_lines.add(node.lineno)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == self.symbol or alias.asname == self.symbol:
                self.reference_lines.add(node.lineno)
            imported_name = alias.name.rsplit(".", maxsplit=1)[-1]
            if imported_name in self.method_owner_classes:
                local_name = alias.asname or imported_name
                self.scope_stack[-1][local_name] = {imported_name}
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == self.symbol or alias.asname == self.symbol:
                self.reference_lines.add(node.lineno)
            if alias.name in self.method_owner_classes:
                local_name = alias.asname or alias.name
                self.scope_stack[-1][local_name] = {alias.name}
        self.generic_visit(node)

    def _attribute_matches_target(self, node: ast.Attribute) -> bool:
        if not self.method_owner_classes:
            return True
        inferred_types = self._infer_expr_types(node.value)
        return bool(inferred_types & self.method_owner_classes)

    def _record_assignment_target(self, target: ast.expr, inferred_types: set[str]) -> None:
        if not inferred_types:
            return
        if isinstance(target, ast.Name):
            self.scope_stack[-1][target.id] = set(inferred_types)
            return
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and self.current_class is not None
        ):
            class_attrs = self.self_attr_types_by_class.setdefault(self.current_class, {})
            class_attrs[target.attr] = set(inferred_types)

    def _infer_expr_types(self, expr: ast.AST | None) -> set[str]:
        if expr is None:
            return set()
        if isinstance(expr, ast.Name):
            return set(self._lookup_name_types(expr.id))
        if isinstance(expr, ast.Call):
            if isinstance(expr.func, ast.Name):
                if not self.method_owner_classes or expr.func.id in self.method_owner_classes:
                    return {expr.func.id}
                return set(self._lookup_name_types(expr.func.id))
            return self._infer_expr_types(expr.func)
        if isinstance(expr, ast.Attribute):
            if isinstance(expr.value, ast.Name) and expr.value.id == "self":
                if self.current_class is None:
                    return set()
                class_attrs = self.self_attr_types_by_class.get(self.current_class, {})
                return set(class_attrs.get(expr.attr, set()))
            return set()
        return set()

    def _infer_annotation_types(self, annotation: ast.AST | None) -> set[str]:
        if annotation is None:
            return set()
        if isinstance(annotation, ast.Name):
            if annotation.id in self.method_owner_classes:
                return {annotation.id}
            return set(self._lookup_name_types(annotation.id))
        if isinstance(annotation, ast.Attribute):
            if annotation.attr in self.method_owner_classes:
                return {annotation.attr}
            return set()
        if isinstance(annotation, ast.Subscript):
            inferred = self._infer_annotation_types(annotation.value)
            if inferred:
                return inferred
            return self._infer_annotation_types(annotation.slice)
        if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            return self._infer_annotation_types(annotation.left) | self._infer_annotation_types(annotation.right)
        if isinstance(annotation, ast.Tuple):
            inferred: set[str] = set()
            for item in annotation.elts:
                inferred |= self._infer_annotation_types(item)
            return inferred
        if hasattr(ast, "Constant") and isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            return self._infer_string_annotation_types(annotation.value)
        return set()

    def _infer_string_annotation_types(self, annotation_text: str) -> set[str]:
        names = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", annotation_text)
        inferred: set[str] = set()
        for name in names:
            if name in self.method_owner_classes:
                inferred.add(name)
                continue
            inferred |= set(self._lookup_name_types(name))
        return inferred

    def _lookup_name_types(self, name: str) -> set[str]:
        for scope in reversed(self.scope_stack):
            if name in scope:
                return scope[name]
        return set()
