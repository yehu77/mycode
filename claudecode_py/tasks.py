from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from threading import Condition
from typing import Any
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class TaskRecord:
    id: str
    kind: str
    description: str
    status: str = "running"
    output: str = ""
    error: str | None = None
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str | None = None
    ended_at: str | None = None
    progress_summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskManager:
    def __init__(self) -> None:
        self._condition = Condition()
        self._tasks: dict[str, TaskRecord] = {}

    def create(self, kind: str, description: str, **metadata: Any) -> TaskRecord:
        task = TaskRecord(id=uuid4().hex[:10], kind=kind, description=description, metadata=metadata)
        with self._condition:
            self._tasks[task.id] = task
            self._condition.notify_all()
        return task

    def complete(self, task_id: str, output: str) -> None:
        with self._condition:
            task = self._tasks[task_id]
            if task.status == "stopped":
                return
            task.status = "completed"
            task.output = output
            task.updated_at = _utc_now_iso()
            task.ended_at = task.updated_at
            self._condition.notify_all()

    def fail(self, task_id: str, error: str) -> None:
        with self._condition:
            task = self._tasks[task_id]
            if task.status == "stopped":
                return
            task.status = "failed"
            task.error = error
            task.updated_at = _utc_now_iso()
            task.ended_at = task.updated_at
            self._condition.notify_all()

    def append_output(self, task_id: str, chunk: str) -> None:
        with self._condition:
            task = self._tasks[task_id]
            if task.status != "running":
                return
            task.output += chunk
            task.updated_at = _utc_now_iso()
            self._condition.notify_all()

    def set_progress(self, task_id: str, summary: str, **metadata: Any) -> None:
        with self._condition:
            task = self._tasks[task_id]
            task.progress_summary = summary
            if metadata:
                task.metadata.update(metadata)
            task.updated_at = _utc_now_iso()
            self._condition.notify_all()

    def stop(self, task_id: str) -> TaskRecord:
        with self._condition:
            task = self._tasks[task_id]
            if task.status in {"completed", "failed", "stopped"}:
                return task
            task.status = "stopped"
            task.updated_at = _utc_now_iso()
            task.ended_at = task.updated_at
            self._condition.notify_all()
            return task

    def get(self, task_id: str) -> TaskRecord | None:
        with self._condition:
            return self._tasks.get(task_id)

    def list(self) -> list[TaskRecord]:
        with self._condition:
            return list(self._tasks.values())

    def wait_for_task(self, task_id: str, timeout_sec: float | None = None) -> TaskRecord:
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            if task.status in {"completed", "failed", "stopped"}:
                return task
            self._condition.wait_for(
                lambda: self._tasks[task_id].status in {"completed", "failed", "stopped"},
                timeout=timeout_sec,
            )
            return self._tasks[task_id]
