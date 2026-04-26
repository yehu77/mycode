from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4


VOLATILE_PYCLAUDE_DIRS = {"background_sessions", "sessions", "workspaces", "worktrees"}


@dataclass(slots=True, frozen=True)
class IsolatedWorkspace:
    mode: str
    original_cwd: Path
    effective_cwd: Path


def get_workspace_snapshots_dir(cwd: Path) -> Path:
    return cwd / ".pyclaude" / "workspaces"


def get_workspace_worktrees_dir(cwd: Path) -> Path:
    return cwd / ".pyclaude" / "worktrees"


def prepare_isolated_workspace(cwd: Path, *, label: str = "agent") -> IsolatedWorkspace:
    source = cwd.resolve()
    worktree = _create_git_worktree(source, label=label)
    if worktree is not None:
        return worktree
    return IsolatedWorkspace(
        mode="snapshot",
        original_cwd=source,
        effective_cwd=create_workspace_snapshot(source, label=label),
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


def create_workspace_snapshot(cwd: Path, *, label: str = "agent") -> Path:
    source = cwd.resolve()
    snapshots_dir = get_workspace_snapshots_dir(source)
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    target = snapshots_dir / f"{label}-{uuid4().hex[:8]}"
    shutil.copytree(
        source,
        target,
        ignore=_build_ignore(source, target),
    )
    return target


def _create_git_worktree(cwd: Path, *, label: str) -> IsolatedWorkspace | None:
    git_root = _git_root_for_workspace(cwd)
    if git_root is None or not _git_workspace_clean(git_root):
        return None
    worktrees_dir = get_workspace_worktrees_dir(cwd.resolve())
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    target = worktrees_dir / f"{label}-{uuid4().hex[:8]}"
    result = _run_git(git_root, "worktree", "add", "--detach", str(target), "HEAD")
    if result.returncode != 0 or not target.exists():
        return None
    _copy_workspace_support_files(cwd.resolve(), target.resolve())
    return IsolatedWorkspace(
        mode="worktree",
        original_cwd=cwd.resolve(),
        effective_cwd=target.resolve(),
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
