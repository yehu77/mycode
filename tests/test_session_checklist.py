from pathlib import Path
import shutil
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.permissions import PermissionManager
from claudecode_py.session import Session
from claudecode_py.storage.session_checklist import SessionChecklistStore
from claudecode_py.tools.base import ToolContext
from claudecode_py.tools.session_task_tools import (
    SessionTaskCreateTool,
    SessionTaskGetTool,
    SessionTaskListTool,
    SessionTaskUpdateTool,
    TodoWriteTool,
)


def _make_tmp_dir(prefix: str) -> Path:
    root = Path(__file__).resolve().parent / "_tmp"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cleanup_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


class SessionChecklistStoreTests(unittest.TestCase):
    def test_store_create_update_and_reload_round_trip(self) -> None:
        cwd = _make_tmp_dir("session_checklist_store")
        try:
            store = SessionChecklistStore(cwd, session_id="session-1")
            created = store.create_task(
                subject="Map runtime",
                description="Inspect session.py",
                active_form="Inspecting session.py",
                metadata={"priority": "high"},
            )
            second = store.create_task(
                subject="Run tests",
                description="Verify the patch",
                active_form="Running tests",
            )

            updated, updated_fields, status_change = store.update_task(
                created.id,
                status="in_progress",
                add_blocks=[second.id],
                metadata={"priority": "urgent", "obsolete": None},
            )

            assert updated is not None
            self.assertEqual(updated.status, "in_progress")
            self.assertIn("blocks", updated_fields)
            self.assertIn("metadata", updated_fields)
            self.assertEqual(status_change, {"from": "pending", "to": "in_progress"})
            self.assertEqual(updated.metadata, {"priority": "urgent"})

            store.save()
            reloaded = SessionChecklistStore(cwd, session_id="session-1")
            tasks = reloaded.list_tasks()

            self.assertEqual([task.id for task in tasks], ["1", "2"])
            self.assertEqual(tasks[0].blocks, ["2"])
            self.assertEqual(tasks[1].blocked_by, ["1"])
            self.assertEqual(reloaded.stats()["in_progress"], 1)
        finally:
            _cleanup_dir(cwd)

    def test_store_delete_removes_dependency_references(self) -> None:
        cwd = _make_tmp_dir("session_checklist_delete")
        try:
            store = SessionChecklistStore(cwd, session_id="session-2")
            first = store.create_task(
                subject="One",
                description="One",
                active_form="Doing one",
            )
            second = store.create_task(
                subject="Two",
                description="Two",
                active_form="Doing two",
                blocked_by=[first.id],
            )
            store.update_task(first.id, add_blocks=[second.id])

            deleted, updated_fields, status_change = store.update_task(first.id, status="deleted")

            self.assertIsNone(deleted)
            self.assertEqual(updated_fields, ["deleted"])
            self.assertEqual(status_change, {"from": "pending", "to": "deleted"})
            remaining = store.list_tasks()
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0].blocked_by, [])
        finally:
            _cleanup_dir(cwd)


class SessionChecklistToolTests(unittest.TestCase):
    def test_session_task_tools_and_todo_write_share_store_without_touching_background_tasks(self) -> None:
        cwd = _make_tmp_dir("session_checklist_tools")
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        background = session.task_manager.create("agent", "background work")
        ctx = ToolContext(
            cwd=cwd,
            permission_manager=PermissionManager(interactive=False),
            task_manager=session.task_manager,
            session=session,
        )

        try:
            created = SessionTaskCreateTool().execute(
                {
                    "subject": "Inspect runtime",
                    "description": "Review session and query loop",
                    "active_form": "Reviewing runtime",
                    "metadata": {"area": "runtime"},
                },
                ctx,
            )
            task_id = created["task"]["id"]

            listed = SessionTaskListTool().execute({}, ctx)
            fetched = SessionTaskGetTool().execute({"task_id": task_id}, ctx)
            updated = SessionTaskUpdateTool().execute(
                {
                    "task_id": task_id,
                    "status": "in_progress",
                    "owner": "main-agent",
                    "metadata": {"area": "runtime", "phase": "inspect"},
                },
                ctx,
            )
            todo_result = TodoWriteTool().execute(
                {
                    "todos": [
                        {
                            "content": "Map runtime",
                            "status": "pending",
                            "active_form": "Mapping runtime",
                        },
                        {
                            "content": "Run tests",
                            "status": "in_progress",
                            "activeForm": "Running tests",
                        },
                    ]
                },
                ctx,
            )

            self.assertEqual(listed["tasks"][0]["subject"], "Inspect runtime")
            self.assertEqual(fetched["task"]["id"], task_id)
            self.assertEqual(updated["status_change"], {"from": "pending", "to": "in_progress"})
            self.assertEqual(updated["task"]["owner"], "main-agent")
            self.assertEqual(todo_result["new_todos"][1]["active_form"], "Running tests")
            self.assertEqual([task["subject"] for task in session.checklist_tasks_payload()], ["Map runtime", "Run tests"])
            self.assertEqual(session.task_manager.get(background.id).description, "background work")
            self.assertEqual(len(session.task_manager.list()), 1)
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_session_task_create_returns_duplicate_guard_for_obvious_duplicate(self) -> None:
        cwd = _make_tmp_dir("session_checklist_duplicate_guard")
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        ctx = ToolContext(
            cwd=cwd,
            permission_manager=PermissionManager(interactive=False),
            task_manager=session.task_manager,
            session=session,
        )

        try:
            original = SessionTaskCreateTool().execute(
                {
                    "subject": "Inspect runtime",
                    "description": "Review session and query loop",
                    "active_form": "Reviewing runtime",
                },
                ctx,
            )
            duplicate = SessionTaskCreateTool().execute(
                {
                    "subject": "  Inspect   runtime  ",
                    "description": "Review session and query loop",
                    "active_form": "Reviewing runtime",
                },
                ctx,
            )

            self.assertTrue(original["created"])
            self.assertFalse(duplicate["created"])
            self.assertEqual(duplicate["task"]["id"], original["task"]["id"])
            self.assertEqual(
                duplicate["duplicate_guard"]["matched_task_id"],
                original["task"]["id"],
            )
            self.assertIn("Possible duplicate checklist task.", duplicate["message"])
            self.assertEqual(len(session.checklist_tasks_payload()), 1)
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_session_task_create_allows_distinct_subject_or_description(self) -> None:
        cwd = _make_tmp_dir("session_checklist_non_duplicate")
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        ctx = ToolContext(
            cwd=cwd,
            permission_manager=PermissionManager(interactive=False),
            task_manager=session.task_manager,
            session=session,
        )

        try:
            first = SessionTaskCreateTool().execute(
                {
                    "subject": "Inspect runtime",
                    "description": "Review session and query loop",
                    "active_form": "Reviewing runtime",
                },
                ctx,
            )
            second = SessionTaskCreateTool().execute(
                {
                    "subject": "Inspect runtime",
                    "description": "Review bridge service",
                    "active_form": "Reviewing bridge service",
                },
                ctx,
            )

            self.assertTrue(first["created"])
            self.assertTrue(second["created"])
            self.assertNotEqual(first["task"]["id"], second["task"]["id"])
            self.assertEqual(len(session.checklist_tasks_payload()), 2)
        finally:
            session.close()
            _cleanup_dir(cwd)


if __name__ == "__main__":
    unittest.main()
