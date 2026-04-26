import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.runtime.events import RuntimeEvent
from claudecode_py.session import Session
from claudecode_py.tasks import TaskManager


class TaskManagerTests(unittest.TestCase):
    def test_task_manager_lifecycle(self) -> None:
        manager = TaskManager()
        task = manager.create("agent", "demo")
        self.assertEqual(task.status, "running")

        manager.complete(task.id, "ok")
        stored = manager.get(task.id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, "completed")
        self.assertEqual(stored.output, "ok")

    def test_stop_prevents_later_completion_override(self) -> None:
        manager = TaskManager()
        task = manager.create("agent", "demo")
        stopped = manager.stop(task.id)
        self.assertEqual(stopped.status, "stopped")

        manager.complete(task.id, "late output")
        stored = manager.get(task.id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, "stopped")
        self.assertEqual(stored.output, "")

    def test_task_manager_wait_for_task(self) -> None:
        manager = TaskManager()
        task = manager.create("agent", "demo")

        manager.complete(task.id, "ok")
        waited = manager.wait_for_task(task.id, timeout_sec=0.1)

        self.assertEqual(waited.status, "completed")
        self.assertEqual(waited.output, "ok")

    def test_background_task_sink_records_progress_and_output(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        task = session.task_manager.create("agent", "demo")
        sink = session._build_background_task_sink(task.id)

        sink(RuntimeEvent(kind="tool_started", message='{"path":"demo.py"}', tool_name="read_file"))
        sink(RuntimeEvent(kind="assistant_text", message="partial answer"))

        stored = session.task_manager.get(task.id)
        assert stored is not None
        self.assertIn("[tool:start] read_file", stored.output)
        self.assertIn("[assistant] partial answer", stored.output)
        self.assertEqual(stored.progress_summary, "[assistant] partial answer")


if __name__ == "__main__":
    unittest.main()
