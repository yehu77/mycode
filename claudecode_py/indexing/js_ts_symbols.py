from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
import re


_IGNORED_DIR_NAMES = {
    ".git",
    ".pyclaude",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
}

_JS_TS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}

_CLASS_RE = re.compile(r"^\s*(?:export\s+default\s+|export\s+)?class\s+([A-Za-z_$][\w$]*)\b")
_FUNCTION_RE = re.compile(
    r"^\s*(?:export\s+default\s+|export\s+)?function\s+([A-Za-z_$][\w$]*)\b"
)
_CONST_ARROW_RE = re.compile(
    r"^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
)
_CONST_FUNCTION_RE = re.compile(
    r"^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?function\b"
)
_METHOD_RE = re.compile(
    r"^\s*(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^;=]*\)\s*\{?\s*$"
)
_IMPORT_FROM_RE = re.compile(
    r"""^\s*(?:import|export)\s+.+?\s+from\s+['"]([^'"]+)['"]"""
)
_IMPORT_SIDE_EFFECT_RE = re.compile(r"""^\s*import\s+['"]([^'"]+)['"]""")
_REQUIRE_RE = re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""")
_CALL_RE = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
_METHOD_CALL_RE = re.compile(r"\.\s*([A-Za-z_$][\w$]*)\s*\(")


@dataclass(slots=True, frozen=True)
class JsTsSymbolEntry:
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
class JsTsImportEntry:
    rel_path: str
    line: int
    module: str

    def render(self) -> str:
        return f"{self.rel_path}:{self.line}:import {self.module}"


@dataclass(slots=True, frozen=True)
class JsTsCallEntry:
    rel_path: str
    line: int
    caller_name: str
    callee_name: str
    caller_owner: str | None = None

    def render(self) -> str:
        caller = f"{self.caller_owner}.{self.caller_name}" if self.caller_owner else self.caller_name
        return f"{self.rel_path}:{self.line}:{caller} -> {self.callee_name}"


@dataclass(slots=True, frozen=True)
class JsTsProjectIndex:
    root: Path
    signature: tuple[tuple[str, int, int], ...]
    entries: tuple[JsTsSymbolEntry, ...]
    imports: tuple[JsTsImportEntry, ...]
    calls: tuple[JsTsCallEntry, ...]
    indexed_files: int

    def find(
        self,
        symbol: str,
        *,
        path_filter: str | None = None,
        max_results: int = 50,
    ) -> list[JsTsSymbolEntry]:
        results: list[JsTsSymbolEntry] = []
        for entry in self.entries:
            if entry.name != symbol:
                continue
            if path_filter is not None and not _path_matches(entry.rel_path, path_filter):
                continue
            results.append(entry)
            if len(results) >= max_results:
                break
        return results

    def imports_for_path(self, rel_path: str) -> list[JsTsImportEntry]:
        return [entry for entry in self.imports if entry.rel_path == rel_path]

    def importers_for_module(self, module: str, *, path_filter: str | None = None) -> list[JsTsImportEntry]:
        results: list[JsTsImportEntry] = []
        for entry in self.imports:
            if entry.module != module:
                continue
            if path_filter is not None and not _path_matches(entry.rel_path, path_filter):
                continue
            results.append(entry)
        return results

    def calls_for_callee(self, callee_name: str, *, path_filter: str | None = None) -> list[JsTsCallEntry]:
        results: list[JsTsCallEntry] = []
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
    ) -> list[JsTsCallEntry]:
        results: list[JsTsCallEntry] = []
        for entry in self.calls:
            if entry.caller_name != caller_name:
                continue
            if caller_owner is not None and entry.caller_owner != caller_owner:
                continue
            if path_filter is not None and not _path_matches(entry.rel_path, path_filter):
                continue
            results.append(entry)
        return results


def build_js_ts_project_index(root: Path) -> JsTsProjectIndex:
    signature: list[tuple[str, int, int]] = []
    entries: list[JsTsSymbolEntry] = []
    imports: list[JsTsImportEntry] = []
    calls: list[JsTsCallEntry] = []
    indexed_files = 0

    for path in _iter_js_ts_files(root):
        rel_path = path.relative_to(root).as_posix()
        stat = path.stat()
        signature.append((rel_path, stat.st_mtime_ns, stat.st_size))
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        indexed_files += 1
        entries.extend(_collect_js_ts_entries(text, rel_path))
        imports.extend(_collect_js_ts_imports(text, rel_path))
        calls.extend(_collect_js_ts_calls(text, rel_path))

    signature.sort()
    entries.sort(key=lambda item: (item.rel_path, item.line, item.owner or "", item.kind, item.name))
    imports.sort(key=lambda item: (item.rel_path, item.line, item.module))
    calls.sort(key=lambda item: (item.rel_path, item.line, item.caller_owner or "", item.caller_name, item.callee_name))
    return JsTsProjectIndex(
        root=root,
        signature=tuple(signature),
        entries=tuple(entries),
        imports=tuple(imports),
        calls=tuple(calls),
        indexed_files=indexed_files,
    )


def snapshot_js_ts_tree(root: Path) -> tuple[tuple[str, int, int], ...]:
    signature: list[tuple[str, int, int]] = []
    for path in _iter_js_ts_files(root):
        rel_path = path.relative_to(root).as_posix()
        stat = path.stat()
        signature.append((rel_path, stat.st_mtime_ns, stat.st_size))
    signature.sort()
    return tuple(signature)


def _iter_js_ts_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _JS_TS_SUFFIXES:
            continue
        if any(part in _IGNORED_DIR_NAMES for part in path.relative_to(root).parts[:-1]):
            continue
        yield path


def _collect_js_ts_entries(text: str, rel_path: str) -> list[JsTsSymbolEntry]:
    entries: list[JsTsSymbolEntry] = []
    class_stack: list[tuple[str, int]] = []

    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue

        indent = len(line) - len(line.lstrip())
        while class_stack and indent <= class_stack[-1][1] and stripped.startswith("}"):
            class_stack.pop()

        if match := _CLASS_RE.match(line):
            name = match.group(1)
            entries.append(JsTsSymbolEntry(name=name, kind="class", rel_path=rel_path, line=line_no))
            class_stack.append((name, indent))
            continue

        if match := _FUNCTION_RE.match(line):
            name = match.group(1)
            entries.append(JsTsSymbolEntry(name=name, kind="function", rel_path=rel_path, line=line_no))
            continue

        if match := _CONST_ARROW_RE.match(line):
            name = match.group(1)
            entries.append(JsTsSymbolEntry(name=name, kind="const =>", rel_path=rel_path, line=line_no))
            continue

        if match := _CONST_FUNCTION_RE.match(line):
            name = match.group(1)
            entries.append(
                JsTsSymbolEntry(name=name, kind="const function", rel_path=rel_path, line=line_no)
            )
            continue

        if class_stack and not stripped.startswith(("if ", "for ", "while ", "switch ", "catch ", "return ")):
            match = _METHOD_RE.match(line)
            if match:
                name = match.group(1)
                if name not in {"constructor", "if", "for", "while", "switch", "catch"}:
                    entries.append(
                        JsTsSymbolEntry(
                            name=name,
                            kind="method",
                            rel_path=rel_path,
                            line=line_no,
                            owner=class_stack[-1][0],
                        )
                    )

    return entries


def _collect_js_ts_imports(text: str, rel_path: str) -> list[JsTsImportEntry]:
    imports: list[JsTsImportEntry] = []
    current_module = _module_id_from_rel_path(rel_path)
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        specifier: str | None = None
        if match := _IMPORT_FROM_RE.match(line):
            specifier = match.group(1)
        elif match := _IMPORT_SIDE_EFFECT_RE.match(line):
            specifier = match.group(1)
        elif match := _REQUIRE_RE.search(line):
            specifier = match.group(1)
        if specifier is None:
            continue
        imports.append(
            JsTsImportEntry(
                rel_path=rel_path,
                line=line_no,
                module=_resolve_js_ts_module_id(current_module, specifier),
            )
        )
    return imports


def _collect_js_ts_calls(text: str, rel_path: str) -> list[JsTsCallEntry]:
    calls: list[JsTsCallEntry] = []
    class_stack: list[tuple[str, int]] = []
    function_stack: list[str] = []

    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue

        indent = len(line) - len(line.lstrip())
        while class_stack and indent <= class_stack[-1][1] and stripped.startswith("}"):
            class_stack.pop()

        if match := _CLASS_RE.match(line):
            class_stack.append((match.group(1), indent))
            continue

        function_name = None
        scan_line = line
        if match := _FUNCTION_RE.match(line):
            function_name = match.group(1)
            scan_line = line[match.end() :]
        elif match := _CONST_ARROW_RE.match(line):
            function_name = match.group(1)
            scan_line = line[match.end() :]
        elif match := _CONST_FUNCTION_RE.match(line):
            function_name = match.group(1)
            scan_line = line[match.end() :]
        elif class_stack:
            match = _METHOD_RE.match(line)
            if match:
                candidate = match.group(1)
                if candidate not in {"constructor", "if", "for", "while", "switch", "catch"}:
                    function_name = candidate
                    scan_line = line[match.end() :]

        if function_name is not None:
            function_stack = [function_name]

        if stripped.startswith("}") and function_stack:
            function_stack = []
            continue

        if not function_stack:
            continue

        caller_name = function_stack[-1]
        caller_owner = class_stack[-1][0] if class_stack else None
        seen: set[str] = set()
        for regex in (_METHOD_CALL_RE, _CALL_RE):
            for match in regex.finditer(scan_line):
                callee = match.group(1)
                if callee in {"if", "for", "while", "switch", "catch", "function", "return"}:
                    continue
                if callee == caller_name and regex is _CALL_RE:
                    pass
                key = f"{callee}:{regex.pattern}"
                if key in seen:
                    continue
                seen.add(key)
                calls.append(
                    JsTsCallEntry(
                        rel_path=rel_path,
                        line=line_no,
                        caller_name=caller_name,
                        caller_owner=caller_owner,
                        callee_name=callee,
                    )
                )
    return calls


def _module_id_from_rel_path(rel_path: str) -> str:
    path = PurePosixPath(rel_path)
    without_suffix = str(path.with_suffix(""))
    if without_suffix.endswith("/index"):
        return without_suffix[: -len("/index")]
    return without_suffix


def _resolve_js_ts_module_id(current_module: str, specifier: str) -> str:
    if not specifier.startswith("."):
        return specifier
    current_path = PurePosixPath(current_module)
    base_dir = current_path.parent
    resolved = (base_dir / specifier).as_posix()
    normalized = str(PurePosixPath(resolved))
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.endswith("/index"):
        normalized = normalized[: -len("/index")]
    return normalized


def _path_matches(rel_path: str, path_filter: str) -> bool:
    normalized = path_filter.replace("\\", "/").strip("./")
    if not normalized:
        return True
    if rel_path == normalized:
        return True
    return rel_path.startswith(normalized.rstrip("/") + "/")
