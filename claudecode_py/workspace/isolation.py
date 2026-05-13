from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4


VOLATILE_PYCLAUDE_DIRS = {"background_sessions", "sessions", "workspaces", "worktrees"}


@dataclass(slots=True, frozen=True)
class IsolatedWorkspace:
    mode: str
    label: str
    original_cwd: Path
    effective_cwd: Path
    created_at: str


@dataclass(slots=True, frozen=True)
class OrphanedWorkspaceDiagnostic:
    mode: str
    label: str
    path: Path


def derive_workspace_health(
    *,
    workspace_mode: str,
    workspace_cleanup_status: str,
    workspace_unavailable: bool,
    orphaned: bool = False,
) -> str:
    if orphaned:
        return "orphaned"
    if workspace_unavailable:
        return "unavailable"
    cleanup_status = str(workspace_cleanup_status or "none")
    if cleanup_status == "failed":
        return "cleanup_failed"
    if cleanup_status == "pending" and workspace_mode in {"snapshot", "worktree"}:
        return "cleanup_pending"
    return "healthy"


def get_workspace_snapshots_dir(cwd: Path) -> Path:
    return cwd / ".pyclaude" / "workspaces"


def get_workspace_worktrees_dir(cwd: Path) -> Path:
    return cwd / ".pyclaude" / "worktrees"


def diagnose_orphaned_workspaces(
    cwd: Path,
    *,
    referenced_paths: set[Path] | None = None,
) -> list[OrphanedWorkspaceDiagnostic]:
    root = cwd.resolve()
    references = {_safe_resolve(path) for path in (referenced_paths or set()) if path is not None}
    orphans: list[OrphanedWorkspaceDiagnostic] = []
    for mode, parent in (
        ("snapshot", get_workspace_snapshots_dir(root)),
        ("worktree", get_workspace_worktrees_dir(root)),
    ):
        if not parent.exists():
            continue
        for child in parent.iterdir():
            if not child.is_dir():
                continue
            resolved = _safe_resolve(child)
            if resolved in references:
                continue
            orphans.append(
                OrphanedWorkspaceDiagnostic(
                    mode=mode,
                    label=child.name,
                    path=resolved,
                )
            )
    orphans.sort(key=lambda item: (item.mode, str(item.path)))
    return orphans


def prepare_isolated_workspace(cwd: Path, *, label: str = "agent") -> IsolatedWorkspace:
    source = cwd.resolve()
    worktree = _create_git_worktree(source, label=label)
    if worktree is not None:
        return worktree
    snapshot_label = f"{label}-{uuid4().hex[:8]}"
    return IsolatedWorkspace(
        mode="snapshot",
        label=snapshot_label,
        original_cwd=source,
        effective_cwd=create_workspace_snapshot(source, label=snapshot_label),
        created_at=_utc_now_iso(),
    )


def cleanup_isolated_workspace(workspace: IsolatedWorkspace) -> None:
    target = workspace.effective_cwd.resolve()
    if not _is_workspace_target_safe(workspace.original_cwd, target, mode=workspace.mode):
        return
    if workspace.mode == "worktree":
        _remove_git_worktree(workspace.original_cwd, target)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        return
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def cleanup_orphaned_workspace(original_cwd: Path, orphan: OrphanedWorkspaceDiagnostic) -> None:
    cleanup_isolated_workspace(
        IsolatedWorkspace(
            mode=orphan.mode,
            label=orphan.label,
            original_cwd=original_cwd.resolve(),
            effective_cwd=orphan.path,
            created_at="",
        )
    )


def repair_isolated_workspace(
    original_cwd: Path,
    *,
    label: str,
    preferred_mode: str,
) -> IsolatedWorkspace:
    source = original_cwd.resolve()
    if preferred_mode == "worktree":
        repaired_worktree = _create_git_worktree(source, label=label, preserve_label=True)
        if repaired_worktree is not None:
            return repaired_worktree
    return IsolatedWorkspace(
        mode="snapshot",
        label=label,
        original_cwd=source,
        effective_cwd=create_workspace_snapshot(source, label=label, preserve_label=True),
        created_at=_utc_now_iso(),
    )


def create_workspace_snapshot(
    cwd: Path,
    *,
    label: str = "agent",
    preserve_label: bool = False,
) -> Path:
    source = cwd.resolve()
    snapshots_dir = get_workspace_snapshots_dir(source)
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    target_name = label if preserve_label else f"{label}-{uuid4().hex[:8]}"
    target = snapshots_dir / target_name
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(
        source,
        target,
        ignore=_build_ignore(source, target),
    )
    return target


def _create_git_worktree(
    cwd: Path,
    *,
    label: str,
    preserve_label: bool = False,
) -> IsolatedWorkspace | None:
    git_root = _git_root_for_workspace(cwd)
    if git_root is None or not _git_workspace_clean(git_root):
        return None
    worktrees_dir = get_workspace_worktrees_dir(cwd.resolve())
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    worktree_label = label if preserve_label else f"{label}-{uuid4().hex[:8]}"
    target = worktrees_dir / worktree_label
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    result = _run_git(git_root, "worktree", "add", "--detach", str(target), "HEAD")
    if result.returncode != 0 or not target.exists():
        return None
    _copy_workspace_support_files(cwd.resolve(), target.resolve())
    return IsolatedWorkspace(
        mode="worktree",
        label=worktree_label,
        original_cwd=cwd.resolve(),
        effective_cwd=target.resolve(),
        created_at=_utc_now_iso(),
    )


def _remove_git_worktree(original_cwd: Path, target: Path) -> None:
    git_root = _git_root_for_workspace(original_cwd)
    if git_root is None:
        return
    _run_git(git_root, "worktree", "remove", "--force", str(target))


def _copy_workspace_support_files(source: Path, target: Path) -> None:
    source_pyclaude = source / ".pyclaude"
    if not source_pyclaude.exists():
        return
    target_pyclaude = target / ".pyclaude"
    target_pyclaude.mkdir(parents=True, exist_ok=True)
    for child in source_pyclaude.iterdir():
        if child.name in VOLATILE_PYCLAUDE_DIRS:
            continue
        destination = target_pyclaude / child.name
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, destination)


def _build_ignore(source: Path, target: Path):
    source = source.resolve()
    target = target.resolve()

    def ignore(directory: str, names: list[str]) -> set[str]:
        directory_path = Path(directory).resolve()
        ignored: set[str] = set()

        if directory_path == source:
            if ".git" in names:
                ignored.add(".git")
        if directory_path == source / ".pyclaude":
            for name in VOLATILE_PYCLAUDE_DIRS:
                if name in names:
                    ignored.add(name)

        for name in names:
            if name in {"__pycache__", ".pytest_cache"}:
                ignored.add(name)
                continue
            candidate = directory_path / name
            try:
                candidate.relative_to(target)
            except ValueError:
                continue
            ignored.add(name)
        return ignored

    return ignore


def _git_root_for_workspace(cwd: Path) -> Path | None:
    result = _run_git(cwd, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        return None
    root = Path(result.stdout.strip())
    if not root.exists():
        return None
    return root.resolve()


def _git_workspace_clean(git_root: Path) -> bool:
    result = _run_git(git_root, "status", "--porcelain")
    return result.returncode == 0 and not result.stdout.strip()


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _is_workspace_target_safe(original_cwd: Path, target: Path, *, mode: str) -> bool:
    roots = {
        "snapshot": get_workspace_snapshots_dir(original_cwd.resolve()),
        "worktree": get_workspace_worktrees_dir(original_cwd.resolve()),
    }
    expected_root = roots.get(mode)
    if expected_root is None:
        return False
    try:
        target.resolve().relative_to(expected_root.resolve())
    except ValueError:
        return False
    return True


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()
