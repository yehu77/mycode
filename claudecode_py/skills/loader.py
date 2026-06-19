from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias

FrontmatterValue: TypeAlias = str | list[str]

_DIRECTORY_SKILLS_DIR = ".claude"
_LEGACY_SKILLS_DIR = ".pyclaude"
_VALID_SKILL_EFFORT_VALUES = frozenset({"low", "medium", "high", "max"})
_TASK_TOOL_NAMES = (
    "todo_write",
    "session_task_create",
    "session_task_list",
    "session_task_get",
    "session_task_update",
    "task_list",
    "task_get",
    "task_stop",
    "task_wait",
)
_KNOWN_LOCAL_TOOL_IDS = {
    "agent",
    "apply_patch",
    "ask_user_question",
    "bash",
    "edit_file",
    "find_callees",
    "find_callers",
    "find_references",
    "find_symbol",
    "find_symbol_graph",
    "glob",
    "grep",
    "list_dir",
    "list_mcp_resources",
    "outline_file",
    "outline_project",
    "read_file",
    "read_mcp_resource",
    "session_task_create",
    "session_task_get",
    "session_task_list",
    "session_task_update",
    "skill",
    "task_get",
    "task_list",
    "task_stop",
    "task_wait",
    "todo_write",
    "tool_search",
    "write_file",
}


@dataclass(slots=True, frozen=True)
class LoadedSkill:
    name: str
    path: Path
    skill_root: Path
    content: str
    description: str = ""
    when_to_use: str = ""
    user_invocable: bool = False
    argument_hint: str = ""
    arguments: tuple[str, ...] = ()
    allowed_tool_names: tuple[str, ...] = ()
    allowed_bash_command_prefixes: tuple[str, ...] = ()
    execution_context: str = "inline"
    disable_model_invocation: bool = False
    model: str = ""
    effort: str = ""
    auto_enable: bool = False
    tags: tuple[str, ...] = ()
    source: str = "project-local"
    source_owner: str = "workspace"


@dataclass(slots=True, frozen=True)
class SkillLoadDiagnostic:
    name: str
    source: str
    path: Path
    error: str
    source_owner: str = "workspace"


@dataclass(slots=True, frozen=True)
class ProjectContext:
    memory_path: Path | None = None
    memory_content: str = ""
    skills: list[LoadedSkill] = field(default_factory=list)
    skill_diagnostics: list[SkillLoadDiagnostic] = field(default_factory=list)


def load_project_context(cwd: Path) -> ProjectContext:
    memory_path, memory_content = _load_project_memory(cwd)
    skills, diagnostics = _load_skills(cwd)
    return ProjectContext(
        memory_path=memory_path,
        memory_content=memory_content,
        skills=skills,
        skill_diagnostics=diagnostics,
    )


def _load_project_memory(cwd: Path) -> tuple[Path | None, str]:
    candidates = [
        cwd / "CLAUDE.md",
        cwd / ".pyclaude" / "memory.md",
    ]
    for path in candidates:
        if path.exists() and path.is_file():
            return path, path.read_text(encoding="utf-8").strip()
    return None, ""


def _load_skills(cwd: Path) -> tuple[list[LoadedSkill], list[SkillLoadDiagnostic]]:
    loaded: list[LoadedSkill] = []
    diagnostics: list[SkillLoadDiagnostic] = []
    seen_skill_names: dict[str, LoadedSkill] = {}
    for path, skill_name, source_format in _iter_project_skill_paths(cwd):
        try:
            skill, skill_diagnostics = _load_skill(path, skill_name=skill_name, source_format=source_format)
        except Exception as exc:
            diagnostics.append(
                SkillLoadDiagnostic(
                    name=skill_name,
                    source="project-local",
                    path=path,
                    error=f"Failed to load skill: {exc}",
                    source_owner="workspace",
                )
            )
            continue
        diagnostics.extend(skill_diagnostics)
        existing = seen_skill_names.get(skill.name)
        if existing is not None:
            diagnostics.append(
                SkillLoadDiagnostic(
                    name=skill.name,
                    source="project-local",
                    path=path,
                    error=(
                        "Skill name conflict: "
                        f'"{skill.name}" from "{path}" conflicts with project-local skill at "{existing.path}".'
                    ),
                    source_owner="workspace",
                )
            )
            continue
        loaded.append(skill)
        seen_skill_names[skill.name] = skill
    return loaded, diagnostics


def _iter_project_skill_paths(cwd: Path) -> list[tuple[Path, str, str]]:
    entries: list[tuple[Path, str, str]] = []
    directory_skills_dir = cwd / _DIRECTORY_SKILLS_DIR / "skills"
    if directory_skills_dir.exists() and directory_skills_dir.is_dir():
        for child in sorted(directory_skills_dir.iterdir(), key=lambda item: item.name.casefold()):
            path = child / "SKILL.md"
            if child.is_dir() and path.exists() and path.is_file():
                entries.append((path, child.name, "directory"))
    legacy_skills_dir = cwd / _LEGACY_SKILLS_DIR / "skills"
    if legacy_skills_dir.exists() and legacy_skills_dir.is_dir():
        for path in sorted(legacy_skills_dir.glob("*.md"), key=lambda item: item.name.casefold()):
            if path.is_file():
                entries.append((path, path.stem, "legacy"))
    return entries


def _load_skill(
    path: Path,
    *,
    skill_name: str,
    source_format: str,
) -> tuple[LoadedSkill, list[SkillLoadDiagnostic]]:
    metadata, content = _parse_skill_file(path.read_text(encoding="utf-8"))
    tags = _parse_string_list(metadata.get("tags", ""))
    arguments = _parse_string_list(metadata.get("arguments", []))
    allowed_tool_names, allowed_bash_prefixes, diagnostics = _map_allowed_tools(
        _parse_string_list(metadata.get("allowed-tools", [])),
        skill_name=skill_name,
        path=path,
    )
    effort = _parse_string(metadata.get("effort", "")).casefold()
    if effort and effort not in _VALID_SKILL_EFFORT_VALUES:
        diagnostics.append(
            SkillLoadDiagnostic(
                name=skill_name,
                source="project-local",
                path=path,
                error=(
                    f'Unsupported skill effort "{effort}" ignored. '
                    f"Expected one of: {', '.join(sorted(_VALID_SKILL_EFFORT_VALUES))}."
                ),
                source_owner="workspace",
            )
        )
        effort = ""
    default_user_invocable = source_format == "directory"
    skill_root = path.parent
    skill = LoadedSkill(
        name=skill_name,
        path=path,
        skill_root=skill_root,
        content=content,
        description=_parse_string(metadata.get("description", "")),
        when_to_use=_parse_string(metadata.get("when-to-use", "")),
        user_invocable=_parse_bool(metadata.get("user-invocable", default_user_invocable)),
        argument_hint=_parse_string(metadata.get("argument-hint", "")),
        arguments=tuple(arguments),
        allowed_tool_names=allowed_tool_names,
        allowed_bash_command_prefixes=allowed_bash_prefixes,
        execution_context=_parse_string(metadata.get("context", "inline")) or "inline",
        disable_model_invocation=_parse_bool(
            metadata.get("disable-model-invocation", "false")
        ),
        model=_parse_string(metadata.get("model", "")),
        effort=effort,
        auto_enable=_parse_bool(metadata.get("auto-enable", "false")),
        tags=tuple(tags),
        source="project-local",
        source_owner="workspace",
    )
    return skill, diagnostics


def _parse_skill_file(raw: str) -> tuple[dict[str, FrontmatterValue], str]:
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text.startswith("---\n"):
        return {}, text

    end_marker = text.find("\n---\n", 4)
    if end_marker == -1:
        return {}, text

    metadata_block = text[4:end_marker]
    body = text[end_marker + 5 :].strip()
    return _parse_frontmatter_block(metadata_block), body


def _parse_frontmatter_block(block: str) -> dict[str, FrontmatterValue]:
    metadata: dict[str, FrontmatterValue] = {}
    lines = block.splitlines()
    index = 0
    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or ":" not in raw_line:
            index += 1
            continue
        key, value = raw_line.split(":", 1)
        normalized_key = _normalize_frontmatter_key(key)
        normalized_value = value.strip()
        if normalized_value:
            metadata[normalized_key] = _strip_wrapping_quotes(normalized_value)
            index += 1
            continue
        items: list[str] = []
        index += 1
        while index < len(lines):
            nested = lines[index]
            nested_stripped = nested.strip()
            if not nested_stripped or nested_stripped.startswith("#"):
                index += 1
                continue
            nested_lstripped = nested.lstrip()
            if nested_lstripped.startswith("- "):
                items.append(_strip_wrapping_quotes(nested_lstripped[2:].strip()))
                index += 1
                continue
            if nested.startswith(" ") or nested.startswith("\t"):
                items.append(_strip_wrapping_quotes(nested_stripped))
                index += 1
                continue
            break
        metadata[normalized_key] = items
    return metadata


def _normalize_frontmatter_key(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _strip_wrapping_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_string(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(item.strip() for item in value if str(item).strip())
    return str(value).strip()


def _parse_string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def _map_allowed_tools(
    entries: list[str],
    *,
    skill_name: str,
    path: Path,
) -> tuple[tuple[str, ...], tuple[str, ...], list[SkillLoadDiagnostic]]:
    tool_names: list[str] = []
    bash_prefixes: list[str] = []
    diagnostics: list[SkillLoadDiagnostic] = []
    alias_map = {
        "agent": ("agent",),
        "edit": ("edit_file", "apply_patch"),
        "glob": ("glob",),
        "grep": ("grep",),
        "ls": ("list_dir",),
        "read": ("read_file",),
        "task": _TASK_TOOL_NAMES,
        "write": ("write_file",),
    }
    for raw_entry in entries:
        entry = raw_entry.strip()
        if not entry:
            continue
        entry_lower = entry.casefold()
        if entry_lower in alias_map:
            tool_names.extend(alias_map[entry_lower])
            continue
        if entry_lower in _KNOWN_LOCAL_TOOL_IDS:
            tool_names.append(entry_lower)
            continue
        if entry_lower.startswith("bash"):
            tool_names.append("bash")
            bash_prefixes.extend(_parse_bash_allowed_prefixes(entry))
            continue
        diagnostics.append(
            SkillLoadDiagnostic(
                name=skill_name,
                source="project-local",
                path=path,
                error=f'Unsupported allowed-tools entry "{entry}" ignored.',
                source_owner="workspace",
            )
        )
    return tuple(_dedupe(tool_names)), tuple(_dedupe(bash_prefixes)), diagnostics


def _parse_bash_allowed_prefixes(entry: str) -> list[str]:
    text = entry.strip()
    if "(" not in text or not text.endswith(")"):
        return []
    inner = text[text.find("(") + 1 : -1].strip()
    if not inner:
        return []
    prefixes: list[str] = []
    for raw_prefix in inner.split(","):
        prefix = _strip_wrapping_quotes(raw_prefix.strip())
        if prefix.endswith(":*"):
            prefix = prefix[:-2]
        prefix = prefix.rstrip(":").strip()
        if prefix:
            prefixes.append(prefix)
    return prefixes


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
