from pathlib import Path
import sys
import unittest
import shutil

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.permissions import PermissionManager
from claudecode_py.session import Session
from claudecode_py.tasks import TaskManager
from claudecode_py.tools.base import ToolContext
from claudecode_py.tools.write_file import WriteFileTool


class WriteFileToolTests(unittest.TestCase):
    def test_write_file_creates_new_file(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_write_file"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            tool = WriteFileTool()
            result = tool.execute(
                {"path": "notes/demo.txt", "content": "hello world"},
                ctx,
            )

            self.assertEqual(result, "Created notes/demo.txt")
            self.assertEqual((cwd / "notes" / "demo.txt").read_text(encoding="utf-8"), "hello world")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_write_file_overwrites_existing_file(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_write_file_overwrite"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        target = cwd / "demo.txt"
        target.write_text("old", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            tool = WriteFileTool()
            result = tool.execute(
                {"path": "demo.txt", "content": "new"},
                ctx,
            )

            self.assertEqual(result, "Overwrote demo.txt")
            self.assertEqual(target.read_text(encoding="utf-8"), "new")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_write_file_approval_request_includes_diff_preview(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_write_file_approval"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        target = cwd / "demo.txt"
        target.write_text("old\n", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            request = WriteFileTool().approval_request(
                {"path": "demo.txt", "content": "new\n"},
                ctx,
            )
            self.assertIn("Pending file change", request.details)
            self.assertIn("action: overwrite file", request.details)
            self.assertIn("path: demo.txt", request.details)
            self.assertIn("[diff]", request.details)
            self.assertIn("--- a/demo.txt", request.details)
            self.assertIn("+++ b/demo.txt", request.details)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
