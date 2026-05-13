from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
import json


CHECKLIST_STATUSES = ("pending", "in_progress", "completed")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class ChecklistTask:
    id: str
    subject: str
    description: str
    active_form: str
    status: str = "pending"
    owner: str | None = None
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_checklist_storage_dir(cwd: Path) -> Path:
    return cwd / ".pyclaude" / "checklists"


def get_checklist_path(cwd: Path, session_id: str) -> Path:
    return get_checklist_storage_dir(cwd) / f"{session_id}.json"


class SessionChecklistStore:
    def __init__(
        self,
        cwd: Path,
        *,
        session_id: str,
        task_list_id: str | None = None,
    ) -> None:
        self._cwd = cwd
        self._session_id = session_id
        self._task_list_id = task_list_id or session_id
        self._path = get_checklist_path(cwd, session_id)
        self._lock = Lock()
        self._next_id = 1
        self._tasks: dict[str, ChecklistTask] = {}
        self._load()

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def task_list_id(self) -> str:
        return self._task_list_id

    @property
    def path(self) -> Path:
        return self._path

    def list_tasks(self, *, task_list_id: str | None = None) -> list[ChecklistTask]:
        self._validate_task_list_id(task_list_id)
        with self._lock:
            return [self._clone_task(task) for task in self._ordered_tasks()]

    def get_task(self, task_id: str, *, task_list_id: str | None = None) -> ChecklistTask | None:
        self._validate_task_list_id(task_list_id)
        with self._lock:
            task = self._tasks.get(task_id)
            return self._clone_task(task) if task is not None else None

    def create_task(
        self,
        *,
        subject: str,
        description: str,
        active_form: str,
        status: str = "pending",
        owner: str | None = None,
        blocks: list[str] | None = None,
        blocked_by: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        task_list_id: str | None = None,
    ) -> ChecklistTask:
        self._validate_task_list_id(task_list_id)
        normalized_status = self._validate_status(status)
        with self._lock:
            task_id = str(self._next_id)
            self._next_id += 1
            task = ChecklistTask(
                id=task_id,
                subject=subject,
                description=description,
                active_form=active_form,
                status=normalized_status,
                owner=owner.strip() if isinstance(owner, str) and owner.strip() else None,
                blocks=self._normalize_string_list(blocks),
                blocked_by=self._normalize_string_list(blocked_by),
                metadata=dict(metadata or {}),
            )
            self._tasks[task_id] = task
            self._persist_locked()
            return self._clone_task(task)

    def update_task(
        self,
        task_id: str,
        *,
        subject: str | None = None,
        description: str | None = None,
        active_form: str | None = None,
        status: str | None = None,
        owner: str | None = None,
        add_blocks: list[str] | None = None,
        add_blocked_by: list[str] | None = None,
        remove_blocks: list[str] | None = None,
        remove_blocked_by: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        task_list_id: str | None = None,
    ) -> tuple[ChecklistTask | None, list[str], dict[str, str] | None]:
        self._validate_task_list_id(task_list_id)
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None, [], None
            updated_fields: list[str] = []
            status_change: dict[str, str] | None = None

            if subject is not None and subject != task.subject:
                task.subject = subject
                updated_fields.append("subject")
            if description is not None and description != task.description:
                task.description = description
                updated_fields.append("description")
            if active_form is not None and active_form != task.active_form:
                task.active_form = active_form
                updated_fields.append("active_form")
            if owner is not None:
                normalized_owner = owner.strip() or None
                if normalized_owner != task.owner:
                    task.owner = normalized_owner
                    updated_fields.append("owner")
            if metadata is not None:
                merged = dict(task.metadata)
                for key, value in metadata.items():
                    if value is None:
                        merged.pop(str(key), None)
                    else:
                        merged[str(key)] = value
                if merged != task.metadata:
                    task.metadata = merged
                    updated_fields.append("metadata")
            if status is not None:
                if status == "deleted":
                    self._delete_task_locked(task_id)
                    return None, ["deleted"], {"from": task.status, "to": "deleted"}
                normalized_status = self._validate_status(status)
                if normalized_status != task.status:
                    status_change = {"from": task.status, "to": normalized_status}
                    task.status = normalized_status
                    updated_fields.append("status")

            if self._apply_dependency_updates(
                task,
                add_blocks=add_blocks,
                add_blocked_by=add_blocked_by,
                remove_blocks=remove_blocks,
                remove_blocked_by=remove_blocked_by,
            ):
                updated_fields.extend(
                    item
                    for item in ("blocks", "blocked_by")
                    if item not in updated_fields
                )

            if updated_fields:
                task.updated_at = _utc_now_iso()
                self._persist_locked()
            return self._clone_task(task), updated_fields, status_change

    def replace_with_todos(
        self,
        todos: list[dict[str, Any]],
        *,
        task_list_id: str | None = None,
    ) -> tuple[list[ChecklistTask], list[ChecklistTask]]:
        self._validate_task_list_id(task_list_id)
        normalized_todos = [self._normalize_todo(item) for item in todos]
        with self._lock:
            old_tasks = [self._clone_task(task) for task in self._ordered_tasks()]
            if normalized_todos and all(item["status"] == "completed" for item in normalized_todos):
                self._tasks.clear()
                self._persist_locked()
                return old_tasks, []

            existing = self._ordered_tasks()
            next_tasks: dict[str, ChecklistTask] = {}
            new_tasks: list[ChecklistTask] = []
            for index, todo in enumerate(normalized_todos):
                existing_task = existing[index] if index < len(existing) else None
                task_id = existing_task.id if existing_task is not None else str(self._next_id)
                if existing_task is None:
                    self._next_id += 1
                    created_at = _utc_now_iso()
                else:
                    created_at = existing_task.created_at
                task = ChecklistTask(
                    id=task_id,
                    subject=todo["content"],
                    description=todo["content"],
                    active_form=todo["active_form"],
                    status=todo["status"],
                    owner=None,
                    blocks=[],
                    blocked_by=[],
                    metadata={},
                    created_at=created_at,
                    updated_at=_utc_now_iso(),
                )
                next_tasks[task.id] = task
                new_tasks.append(self._clone_task(task))
            self._tasks = next_tasks
            self._persist_locked()
            return old_tasks, new_tasks

    def stats(self, *, task_list_id: str | None = None) -> dict[str, int]:
        self._validate_task_list_id(task_list_id)
        with self._lock:
            tasks = self._ordered_tasks()
            return {
                "total": len(tasks),
                "pending": sum(1 for task in tasks if task.status == "pending"),
                "in_progress": sum(1 for task in tasks if task.status == "in_progress"),
                "completed": sum(1 for task in tasks if task.status == "completed"),
            }

    def save(self) -> Path:
        with self._lock:
            self._persist_locked()
            return self._path

    def _load(self) -> None:
        if not self._path.exists():
            return
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        self._task_list_id = str(payload.get("task_list_id") or self._task_list_id)
        self._next_id = int(payload.get("next_id", 1) or 1)
        tasks: dict[str, ChecklistTask] = {}
        for item in payload.get("tasks", []):
            task = ChecklistTask(
                id=str(item.get("id", "")),
                subject=str(item.get("subject", "")),
                description=str(item.get("description", "")),
                active_form=str(item.get("active_form", "")),
                status=self._validate_status(str(item.get("status", "pending"))),
                owner=(
                    str(item.get("owner"))
                    if item.get("owner") not in {None, ""}
                    else None
                ),
                blocks=self._normalize_string_list(item.get("blocks")),
                blocked_by=self._normalize_string_list(item.get("blocked_by")),
                metadata=dict(item.get("metadata") or {}),
                created_at=str(item.get("created_at") or _utc_now_iso()),
                updated_at=(
                    str(item.get("updated_at"))
                    if item.get("updated_at") not in {None, ""}
                    else None
                ),
            )
            if task.id:
                tasks[task.id] = task
        self._tasks = tasks
        highest_numeric = max((self._task_numeric_key(task.id) for task in tasks.values()), default=0)
        self._next_id = max(self._next_id, highest_numeric + 1)

    def _persist_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "session_id": self._session_id,
            "task_list_id": self._task_list_id,
            "next_id": self._next_id,
            "tasks": [task.to_dict() for task in self._ordered_tasks()],
        }
        self._path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def _ordered_tasks(self) -> list[ChecklistTask]:
        return sorted(
            self._tasks.values(),
            key=lambda task: (self._task_numeric_key(task.id), task.id),
        )

    def _delete_task_locked(self, task_id: str) -> bool:
        task = self._tasks.pop(task_id, None)
        if task is None:
            return False
        for other in self._tasks.values():
            if task_id in other.blocks:
                other.blocks = [item for item in other.blocks if item != task_id]
                other.updated_at = _utc_now_iso()
            if task_id in other.blocked_by:
                other.blocked_by = [item for item in other.blocked_by if item != task_id]
                other.updated_at = _utc_now_iso()
        self._persist_locked()
        return True

    def _apply_dependency_updates(
        self,
        task: ChecklistTask,
        *,
        add_blocks: list[str] | None,
        add_blocked_by: list[str] | None,
        remove_blocks: list[str] | None,
        remove_blocked_by: list[str] | None,
    ) -> bool:
        changed = False
        for related_id in self._normalize_string_list(add_blocks):
            if related_id == task.id:
                continue
            related = self._tasks.get(related_id)
            if related is None:
                continue
            if related_id not in task.blocks:
                task.blocks.append(related_id)
                changed = True
            if task.id not in related.blocked_by:
                related.blocked_by.append(task.id)
                related.updated_at = _utc_now_iso()
        for related_id in self._normalize_string_list(add_blocked_by):
            if related_id == task.id:
                continue
            related = self._tasks.get(related_id)
            if related is None:
                continue
            if related_id not in task.blocked_by:
                task.blocked_by.append(related_id)
                changed = True
            if task.id not in related.blocks:
                related.blocks.append(task.id)
                related.updated_at = _utc_now_iso()
        for related_id in self._normalize_string_list(remove_blocks):
            if related_id in task.blocks:
                task.blocks = [item for item in task.blocks if item != related_id]
                changed = True
            related = self._tasks.get(related_id)
            if related is not None and task.id in related.blocked_by:
                related.blocked_by = [item for item in related.blocked_by if item != task.id]
                related.updated_at = _utc_now_iso()
        for related_id in self._normalize_string_list(remove_blocked_by):
            if related_id in task.blocked_by:
                task.blocked_by = [item for item in task.blocked_by if item != related_id]
                changed = True
            related = self._tasks.get(related_id)
            if related is not None and task.id in related.blocks:
                related.blocks = [item for item in related.blocks if item != task.id]
                related.updated_at = _utc_now_iso()
        if changed:
            task.blocks = self._normalize_string_list(task.blocks)
            task.blocked_by = self._normalize_string_list(task.blocked_by)
        return changed

    def _normalize_todo(self, item: dict[str, Any]) -> dict[str, str]:
        content = str(item.get("content", "")).strip()
        if not content:
            raise ValueError("Todo content cannot be empty.")
        active_form = str(item.get("active_form") or item.get("activeForm") or "").strip()
        if not active_form:
            raise ValueError("Todo active_form cannot be empty.")
        return {
            "content": content,
            "active_form": active_form,
            "status": self._validate_status(str(item.get("status", "pending"))),
        }

    def _validate_task_list_id(self, task_list_id: str | None) -> None:
        if task_list_id is None:
            return
        normalized = str(task_list_id).strip()
        if normalized and normalized != self._task_list_id:
            raise ValueError(f"Unsupported task_list_id: {normalized}")

    def _validate_status(self, status: str) -> str:
        normalized = status.strip()
        if normalized not in CHECKLIST_STATUSES:
            raise ValueError(
                f"Invalid checklist task status: {status}. Expected one of: {', '.join(CHECKLIST_STATUSES)}"
            )
        return normalized

    def _normalize_string_list(self, value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)):
            return []
        seen: set[str] = set()
        items: list[str] = []
        for item in value:
            normalized = str(item).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            items.append(normalized)
        return items

    def _clone_task(self, task: ChecklistTask | None) -> ChecklistTask | None:
        if task is None:
            return None
        return ChecklistTask(
            id=task.id,
            subject=task.subject,
            description=task.description,
            active_form=task.active_form,
            status=task.status,
            owner=task.owner,
            blocks=list(task.blocks),
            blocked_by=list(task.blocked_by),
            metadata=dict(task.metadata),
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

    def _task_numeric_key(self, task_id: str) -> int:
        try:
            return int(task_id)
        except ValueError:
            return 10**9
