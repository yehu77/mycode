from __future__ import annotations

from dataclasses import asdict, dataclass
from difflib import SequenceMatcher


@dataclass(slots=True, frozen=True)
class SymbolLocation:
    symbol: str
    kind: str
    path: str
    line: int
    owner: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class ReferenceLocation:
    symbol: str
    path: str
    line: int
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class SymbolLookupResult:
    symbol: str
    matches: tuple[SymbolLocation, ...]

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "matches": [item.to_dict() for item in self.matches],
        }


@dataclass(slots=True, frozen=True)
class ReferenceLookupResult:
    symbol: str
    references: tuple[ReferenceLocation, ...]

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "references": [item.to_dict() for item in self.references],
        }


@dataclass(slots=True, frozen=True)
class EditorTarget:
    action: str
    path: str
    line: int = 1
    column: int = 1
    end_line: int | None = None
    end_column: int | None = None
    label: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class DiffTargetResult:
    path: str
    hunks: tuple[EditorTarget, ...]

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "hunks": [item.to_dict() for item in self.hunks],
        }


@dataclass(slots=True, frozen=True)
class ReferenceTargetResult:
    symbol: str
    targets: tuple[EditorTarget, ...]

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "targets": [item.to_dict() for item in self.targets],
        }


@dataclass(slots=True, frozen=True)
class SymbolActionBundle:
    symbol: str
    definitions: tuple[EditorTarget, ...]
    references: tuple[EditorTarget, ...]

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "definitions": [item.to_dict() for item in self.definitions],
            "references": [item.to_dict() for item in self.references],
        }


def parse_reference_line(symbol: str, rendered: str) -> ReferenceLocation | None:
    path, sep, remainder = rendered.partition(":")
    if not sep:
        return None
    line_text, sep, content = remainder.partition(":")
    if not sep:
        return None
    try:
        line = int(line_text)
    except ValueError:
        return None
    return ReferenceLocation(symbol=symbol, path=path, line=line, text=content)


def build_open_file_target(
    path: str,
    *,
    line: int = 1,
    column: int = 1,
    end_line: int | None = None,
    end_column: int | None = None,
    label: str = "",
) -> EditorTarget:
    return EditorTarget(
        action="open_file",
        path=path,
        line=max(1, line),
        column=max(1, column),
        end_line=end_line,
        end_column=end_column,
        label=label,
    )


def build_symbol_target(location: SymbolLocation) -> EditorTarget:
    owner = f"{location.owner}." if location.owner else ""
    return EditorTarget(
        action="open_symbol",
        path=location.path,
        line=location.line,
        column=1,
        label=f"{owner}{location.kind} {location.symbol}",
    )


def build_diff_targets(path: str, before: str, after: str) -> DiffTargetResult:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    matcher = SequenceMatcher(a=before_lines, b=after_lines)
    targets: list[EditorTarget] = []
    for index, (tag, i1, i2, j1, j2) in enumerate(matcher.get_opcodes(), start=1):
        if tag == "equal":
            continue
        start_line = j1 + 1 if j2 > 0 else max(1, j1 + 1)
        end_line = max(start_line, j2)
        label = f"{tag} lines {start_line}-{end_line}"
        targets.append(
            EditorTarget(
                action="open_diff",
                path=path,
                line=start_line,
                column=1,
                end_line=end_line,
                end_column=1,
                label=label,
            )
        )
    return DiffTargetResult(path=path, hunks=tuple(targets))


def build_reference_targets(lookup: ReferenceLookupResult) -> ReferenceTargetResult:
    targets: list[EditorTarget] = []
    for item in lookup.references:
        snippet = item.text.strip()
        if len(snippet) > 80:
            snippet = snippet[:77] + "..."
        label = snippet or f"{lookup.symbol} reference"
        targets.append(
            EditorTarget(
                action="open_reference",
                path=item.path,
                line=item.line,
                column=1,
                label=label,
            )
        )
    return ReferenceTargetResult(symbol=lookup.symbol, targets=tuple(targets))


def build_symbol_action_bundle(
    lookup: SymbolLookupResult,
    references: ReferenceTargetResult,
) -> SymbolActionBundle:
    definitions = tuple(build_symbol_target(item) for item in lookup.matches)
    return SymbolActionBundle(
        symbol=lookup.symbol,
        definitions=definitions,
        references=references.targets,
    )
