from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import ast


_IGNORED_DIR_NAMES = {
    ".git",
    ".pyclaude",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
}


@dataclass(slots=True, frozen=True)
class PythonSymbolEntry:
    name: str
    kind: str
    rel_path: str
    line: int
    owner: str | None = None

    def render(self) -> str:
        if self.owner:
            return f"{self.rel_path}:{self.line}:{self.owner}.{self.kind} {self.name}"
        return f"{self.rel_path}:{self.line}:{self.kind} {self.name}"


@dataclass(slots=True, frozen=True)
class PythonImportEntry:
    rel_path: str
    line: int
    module: str
    imported_names: tuple[str, ...] = ()

    def render(self) -> str:
        if self.imported_names:
            names = ", ".join(self.imported_names)
            return f"{self.rel_path}:{self.line}:from {self.module} import {names}"
        return f"{self.rel_path}:{self.line}:import {self.module}"


@dataclass(slots=True, frozen=True)
class PythonInheritanceEntry:
    rel_path: str
    line: int
    class_name: str
    bases: tuple[str, ...] = ()

    def render(self) -> str:
        if self.bases:
            return f"{self.rel_path}:{self.line}:class {self.class_name}({', '.join(self.bases)})"
        return f"{self.rel_path}:{self.line}:class {self.class_name}"


@dataclass(slots=True, frozen=True)
class PythonCallEntry:
    rel_path: str
    line: int
    caller_name: str
    callee_name: str
    caller_owner: str | None = None

    def render(self) -> str:
        caller = f"{self.caller_owner}.{self.caller_name}" if self.caller_owner else self.caller_name
        return f"{self.rel_path}:{self.line}:{caller} -> {self.callee_name}"


@dataclass(slots=True, frozen=True)
class PythonProjectIndex:
    root: Path
    signature: tuple[tuple[str, int, int], ...]
    entries: tuple[PythonSymbolEntry, ...]
    imports: tuple[PythonImportEntry, ...]
    inheritances: tuple[PythonInheritanceEntry, ...]
    calls: tuple[PythonCallEntry, ...]
    indexed_files: int

    def find(
        self,
        symbol: str,
        *,
        path_filter: str | None = None,
        max_results: int = 50,
    ) -> list[PythonSymbolEntry]:
        results: list[PythonSymbolEntry] = []
        for entry in self.entries:
            if entry.name != symbol:
                continue
            if path_filter is not None and not _path_matches(entry.rel_path, path_filter):
                continue
            results.append(entry)
            if len(results) >= max_results:
                break
        return results

    def method_owner_classes(self, symbol: str, *, path_filter: str | None = None) -> set[str]:
        owners: set[str] = set()
        for entry in self.entries:
            if entry.name != symbol or entry.owner is None:
                continue
            if path_filter is not None and not _path_matches(entry.rel_path, path_filter):
                continue
            owners.add(entry.owner)
        return owners

    def imports_for_path(self, rel_path: str) -> list[PythonImportEntry]:
        return [entry for entry in self.imports if entry.rel_path == rel_path]

    def importers_for_module(self, module: str, *, path_filter: str | None = None) -> list[PythonImportEntry]:
        results: list[PythonImportEntry] = []
        for entry in self.imports:
            if entry.module != module:
                continue
            if path_filter is not None and not _path_matches(entry.rel_path, path_filter):
                continue
            results.append(entry)
        return results

    def inheritance_for_class(self, class_name: str, *, path_filter: str | None = None) -> list[PythonInheritanceEntry]:
        results: list[PythonInheritanceEntry] = []
        for entry in self.inheritances:
            if entry.class_name != class_name:
                continue
            if path_filter is not None and not _path_matches(entry.rel_path, path_filter):
                continue
            results.append(entry)
        return results

    def derived_classes(self, base_class: str, *, path_filter: str | None = None) -> list[PythonInheritanceEntry]:
        results: list[PythonInheritanceEntry] = []
        for entry in self.inheritances:
            if base_class not in entry.bases:
                continue
            if path_filter is not None and not _path_matches(entry.rel_path, path_filter):
                continue
            results.append(entry)
        return results

    def calls_for_callee(self, callee_name: str, *, path_filter: str | None = None) -> list[PythonCallEntry]:
        results: list[PythonCallEntry] = []
        for entry in self.calls:
            if entry.callee_name != callee_name:
                continue
            if path_filter is not None and not _path_matches(entry.rel_path, path_filter):
                continue
            results.append(entry)
        return results

    def calls_from_caller(
        self,
        caller_name: str,
        *,
        caller_owner: str | None = None,
        path_filter: str | None = None,
    ) -> list[PythonCallEntry]:
        results: list[PythonCallEntry] = []
        for entry in self.calls:
            if entry.caller_name != caller_name:
                continue
            if caller_owner is not None and entry.caller_owner != caller_owner:
                continue
            if path_filter is not None and not _path_matches(entry.rel_path, path_filter):
                continue
            results.append(entry)
        return results


def build_python_project_index(root: Path) -> PythonProjectIndex:
    signature: list[tuple[str, int, int]] = []
    entries: list[PythonSymbolEntry] = []
    imports: list[PythonImportEntry] = []
    inheritances: list[PythonInheritanceEntry] = []
    calls: list[PythonCallEntry] = []
    indexed_files = 0

    for path in _iter_python_files(root):
        rel_path = path.relative_to(root).as_posix()
        stat = path.stat()
        signature.append((rel_path, stat.st_mtime_ns, stat.st_size))
        try:
            text = path.read_text(encoding="utf-8")
            module = ast.parse(text)
        except Exception:  # noqa: BLE001
            continue
        indexed_files += 1
        entries.extend(_collect_python_entries(module, rel_path))
        imports.extend(_collect_python_imports(module, rel_path))
        inheritances.extend(_collect_python_inheritances(module, rel_path))
        calls.extend(_collect_python_calls(module, rel_path))

    signature.sort()
    entries.sort(key=lambda item: (item.rel_path, item.line, item.owner or "", item.kind, item.name))
    imports.sort(key=lambda item: (item.rel_path, item.line, item.module, item.imported_names))
    inheritances.sort(key=lambda item: (item.rel_path, item.line, item.class_name, item.bases))
    calls.sort(key=lambda item: (item.rel_path, item.line, item.caller_owner or "", item.caller_name, item.callee_name))
    return PythonProjectIndex(
        root=root,
        signature=tuple(signature),
        entries=tuple(entries),
        imports=tuple(imports),
        inheritances=tuple(inheritances),
        calls=tuple(calls),
        indexed_files=indexed_files,
    )


def snapshot_python_tree(root: Path) -> tuple[tuple[str, int, int], ...]:
    signature: list[tuple[str, int, int]] = []
    for path in _iter_python_files(root):
        rel_path = path.relative_to(root).as_posix()
        stat = path.stat()
        signature.append((rel_path, stat.st_mtime_ns, stat.st_size))
    signature.sort()
    return tuple(signature)


def _iter_python_files(root: Path):
    for path in root.rglob("*.py"):
        if not path.is_file():
            continue
        if any(part in _IGNORED_DIR_NAMES for part in path.relative_to(root).parts[:-1]):
            continue
        yield path


def _collect_python_entries(module: ast.Module, rel_path: str) -> list[PythonSymbolEntry]:
    entries: list[PythonSymbolEntry] = []
    for node in module.body:
        if isinstance(node, ast.ClassDef):
            entries.append(
                PythonSymbolEntry(
                    name=node.name,
                    kind="class",
                    rel_path=rel_path,
                    line=node.lineno,
                )
            )
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    entries.append(
                        PythonSymbolEntry(
                            name=item.name,
                            kind="async def" if isinstance(item, ast.AsyncFunctionDef) else "def",
                            rel_path=rel_path,
                            line=item.lineno,
                            owner=node.name,
                        )
                    )
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            entries.append(
                PythonSymbolEntry(
                    name=node.name,
                    kind="async def" if isinstance(node, ast.AsyncFunctionDef) else "def",
                    rel_path=rel_path,
                    line=node.lineno,
                )
            )
    return entries


def _collect_python_imports(module: ast.Module, rel_path: str) -> list[PythonImportEntry]:
    imports: list[PythonImportEntry] = []
    current_module = _module_name_from_rel_path(rel_path)
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(
                    PythonImportEntry(
                        rel_path=rel_path,
                        line=node.lineno,
                        module=alias.name,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_from_module(current_module, node.level, node.module)
            if resolved is None:
                continue
            imports.append(
                PythonImportEntry(
                    rel_path=rel_path,
                    line=node.lineno,
                    module=resolved,
                    imported_names=tuple(alias.name for alias in node.names),
                )
            )
    return imports


def _collect_python_inheritances(module: ast.Module, rel_path: str) -> list[PythonInheritanceEntry]:
    inheritances: list[PythonInheritanceEntry] = []
    for node in module.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = tuple(
            rendered
            for rendered in (_render_base_expr(base) for base in node.bases)
            if rendered
        )
        inheritances.append(
            PythonInheritanceEntry(
                rel_path=rel_path,
                line=node.lineno,
                class_name=node.name,
                bases=bases,
            )
        )
    return inheritances


def _collect_python_calls(module: ast.Module, rel_path: str) -> list[PythonCallEntry]:
    collector = _PythonCallCollector(rel_path)
    collector.visit(module)
    return collector.calls


def _module_name_from_rel_path(rel_path: str) -> str:
    if rel_path.endswith("/__init__.py"):
        return rel_path[: -len("/__init__.py")].replace("/", ".")
    if rel_path.endswith(".py"):
        return rel_path[:-3].replace("/", ".")
    return rel_path.replace("/", ".")


def _resolve_from_module(current_module: str, level: int, module: str | None) -> str | None:
    if level <= 0:
        return module
    parts = current_module.split(".")
    package_parts = parts[:-1]
    if level > len(package_parts) + 1:
        return module
    base_parts = package_parts[: len(package_parts) - (level - 1)]
    if module:
        return ".".join([*base_parts, module]) if base_parts else module
    return ".".join(base_parts) if base_parts else None


def _render_base_expr(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts: list[str] = []
        current: ast.AST | None = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            return ".".join(reversed(parts))
        return None
    if isinstance(node, ast.Subscript):
        return _render_base_expr(node.value)
    return None


class _PythonCallCollector(ast.NodeVisitor):
    def __init__(self, rel_path: str) -> None:
        self.rel_path = rel_path
        self.calls: list[PythonCallEntry] = []
        self._class_stack: list[str] = []
        self._function_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if self._function_stack:
            callee = _render_call_target(node.func)
            if callee:
                self.calls.append(
                    PythonCallEntry(
                        rel_path=self.rel_path,
                        line=node.lineno,
                        caller_name=self._function_stack[-1],
                        caller_owner=self._class_stack[-1] if self._class_stack else None,
                        callee_name=callee,
                    )
                )
        self.generic_visit(node)


def _render_call_target(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _path_matches(rel_path: str, path_filter: str) -> bool:
    normalized = path_filter.replace("\\", "/").strip("./")
    if not normalized:
        return True
    if rel_path == normalized:
        return True
    return rel_path.startswith(normalized.rstrip("/") + "/")
