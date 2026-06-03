from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True, frozen=True)
class LoadedSkill:
    name: str
    path: Path
    content: str
    description: str = ""
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
    skills = _load_skills(cwd)
    return ProjectContext(
        memory_path=memory_path,
        memory_content=memory_content,
        skills=skills,
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


def _load_skills(cwd: Path) -> list[LoadedSkill]:
    skills_dir = cwd / ".pyclaude" / "skills"
    if not skills_dir.exists() or not skills_dir.is_dir():
        return []

    loaded: list[LoadedSkill] = []
    for path in sorted(skills_dir.glob("*.md")):
        if not path.is_file():
            continue
        metadata, content = _parse_skill_file(path.read_text(encoding="utf-8"))
        loaded.append(
            LoadedSkill(
                name=path.stem,
                path=path,
                content=content,
                description=metadata.get("description", ""),
                auto_enable=_parse_bool(metadata.get("auto_enable", "false")),
                tags=_parse_tags(metadata.get("tags", "")),
                source="project-local",
                source_owner="workspace",
            )
        )
    return loaded


def _parse_skill_file(raw: str) -> tuple[dict[str, str], str]:
    text = raw.strip()
    if not text.startswith("---\n"):
        return {}, text

    end_marker = text.find("\n---\n", 4)
    if end_marker == -1:
        return {}, text

    metadata_block = text[4:end_marker]
    body = text[end_marker + 5 :].strip()
    metadata: dict[str, str] = {}
    for line in metadata_block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        metadata[key.strip()] = value.strip()
    return metadata, body


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_tags(value: str) -> tuple[str, ...]:
    if not value.strip():
        return ()
    return tuple(tag.strip() for tag in value.split(",") if tag.strip())
