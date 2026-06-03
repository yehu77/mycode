from __future__ import annotations

from dataclasses import asdict, dataclass, field
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

    def complete(self, task_id: str, output: str, **metadata: Any) -> None:
        with self._condition:
            task = self._tasks[task_id]
            if task.status == "stopped":
                return
            task.status = "completed"
            task.output = output
            if metadata:
                task.metadata.update(metadata)
            task.updated_at = _utc_now_iso()
            task.ended_at = task.updated_at
            self._condition.notify_all()

    def fail(self, task_id: str, error: str, **metadata: Any) -> None:
        with self._condition:
            task = self._tasks[task_id]
            if task.status == "stopped":
                return
            task.status = "failed"
            task.error = error
            if metadata:
                task.metadata.update(metadata)
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

    def update_metadata(self, task_id: str, **metadata: Any) -> None:
        if not metadata:
            return
        with self._condition:
            task = self._tasks[task_id]
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

    def snapshot(self) -> list[dict[str, Any]]:
        with self._condition:
            return [serialize_task_record(task) for task in self._tasks.values()]

    def restore_snapshot(
        self,
        payloads: list[object],
        *,
        clear: bool = True,
        normalize_running: bool = True,
    ) -> None:
        records = load_task_records(payloads, normalize_running=normalize_running)
        with self._condition:
            if clear:
                self._tasks = {}
            for record in records:
                self._tasks[record.id] = record
            self._condition.notify_all()

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

    @classmethod
    def from_snapshot(
        cls,
        payloads: list[object],
        *,
        normalize_running: bool = True,
    ) -> TaskManager:
        manager = cls()
        manager.restore_snapshot(payloads, normalize_running=normalize_running)
        return manager


def serialize_task_record(task: TaskRecord) -> dict[str, Any]:
    return asdict(task)


def load_task_records(
    payloads: list[object],
    *,
    normalize_running: bool = True,
) -> list[TaskRecord]:
    records: list[TaskRecord] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        task_id = str(payload.get("id") or "").strip()
        kind = str(payload.get("kind") or "").strip()
        description = str(payload.get("description") or "")
        if not task_id or not kind:
            continue
        metadata = payload.get("metadata")
        restored_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        record = TaskRecord(
            id=task_id,
            kind=kind,
            description=description,
            status=str(payload.get("status", "running") or "running"),
            output=str(payload.get("output", "") or ""),
            error=(str(payload.get("error")) if payload.get("error") is not None else None),
            created_at=str(payload.get("created_at", _utc_now_iso()) or _utc_now_iso()),
            updated_at=(str(payload.get("updated_at")) if payload.get("updated_at") is not None else None),
            ended_at=(str(payload.get("ended_at")) if payload.get("ended_at") is not None else None),
            progress_summary=(
                str(payload.get("progress_summary"))
                if payload.get("progress_summary") is not None
                else None
            ),
            metadata=restored_metadata,
        )
        if normalize_running and record.status == "running":
            record.status = "stopped"
            timestamp = record.updated_at or record.ended_at or _utc_now_iso()
            record.updated_at = timestamp
            record.ended_at = timestamp
            record.metadata.setdefault("restored_from_saved_session", True)
            record.metadata.setdefault(
                "resume_note",
                "Restored from saved session state. Live process execution was not resumed.",
            )
        records.append(record)
    return records
