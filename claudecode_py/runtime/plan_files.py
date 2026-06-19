from __future__ import annotations

from pathlib import Path
import re
import shutil

_PLAN_SLUG_CACHE: dict[str, str] = {}
_SLUG_SANITIZE_PATTERN = re.compile(r"[^a-z0-9-]+")


def get_plan_storage_dir(cwd: Path) -> Path:
    return cwd / ".pyclaude" / "plans"


def default_plan_slug(session_id: str) -> str:
    prefix = (session_id or "session").strip().lower()[:12] or "session"
    return f"plan-{prefix}"


def normalize_plan_slug(slug: str, *, fallback_session_id: str) -> str:
    normalized = _SLUG_SANITIZE_PATTERN.sub("-", str(slug or "").strip().lower()).strip("-")
    return normalized or default_plan_slug(fallback_session_id)


def set_plan_slug(session_id: str, slug: str) -> str:
    normalized = normalize_plan_slug(slug, fallback_session_id=session_id)
    _PLAN_SLUG_CACHE[str(session_id)] = normalized
    return normalized


def clear_plan_slug(session_id: str) -> None:
    _PLAN_SLUG_CACHE.pop(str(session_id), None)


def get_plan_slug(session_id: str, *, existing_slug: str | None = None) -> str:
    key = str(session_id)
    if existing_slug:
        return set_plan_slug(key, existing_slug)
    cached = _PLAN_SLUG_CACHE.get(key)
    if cached:
        return cached
    return set_plan_slug(key, default_plan_slug(key))


def get_plan_file_path(
    cwd: Path,
    session_id: str,
    *,
    agent_id: str | None = None,
    existing_slug: str | None = None,
) -> Path:
    slug = get_plan_slug(session_id, existing_slug=existing_slug)
    if agent_id:
        normalized_agent_id = normalize_plan_slug(agent_id, fallback_session_id=session_id)
        slug = f"{slug}-agent-{normalized_agent_id}"
    return get_plan_storage_dir(cwd) / f"{slug}.md"


def ensure_plan_file(
    cwd: Path,
    session_id: str,
    *,
    agent_id: str | None = None,
    existing_slug: str | None = None,
) -> Path:
    path = get_plan_file_path(
        cwd,
        session_id,
        agent_id=agent_id,
        existing_slug=existing_slug,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    return path


def get_plan(
    cwd: Path,
    session_id: str,
    *,
    agent_id: str | None = None,
    existing_slug: str | None = None,
) -> str:
    path = get_plan_file_path(
        cwd,
        session_id,
        agent_id=agent_id,
        existing_slug=existing_slug,
    )
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def copy_plan_for_resume(cwd: Path, session_id: str, *, existing_slug: str | None = None) -> Path | None:
    path = get_plan_file_path(cwd, session_id, existing_slug=existing_slug)
    if not path.exists():
        return None
    set_plan_slug(session_id, existing_slug or path.stem)
    return path


def copy_plan_for_fork(
    cwd: Path,
    parent_session_id: str,
    child_session_id: str,
    *,
    parent_slug: str | None = None,
    child_slug: str | None = None,
) -> Path | None:
    source = get_plan_file_path(cwd, parent_session_id, existing_slug=parent_slug)
    if not source.exists():
        return None
    target = ensure_plan_file(cwd, child_session_id, existing_slug=child_slug)
    if source.resolve() != target.resolve():
        shutil.copyfile(source, target)
    return target
