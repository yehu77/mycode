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
from claudecode_py.tools.edit_file import EditFileTool


class EditFileToolTests(unittest.TestCase):
    def test_edit_file_single_replace_returns_diff_summary(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_edit_file"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        target = cwd / "demo.txt"
        target.write_text("hello world\n", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            tool = EditFileTool()
            result = tool.execute(
                {"path": "demo.txt", "old_text": "world", "new_text": "agent"},
                ctx,
            )

            self.assertIn("Updated demo.txt (1 replacement)", result)
            self.assertIn("--- a/demo.txt", result)
            self.assertIn("+++ b/demo.txt", result)
            self.assertEqual(target.read_text(encoding="utf-8"), "hello agent\n")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_edit_file_multi_edit_applies_in_order(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_multi_edit"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        target = cwd / "demo.txt"
        target.write_text("alpha beta gamma beta\n", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            tool = EditFileTool()
            result = tool.execute(
                {
                    "path": "demo.txt",
                    "new_text": "",
                    "edits": [
                        {"old_text": "alpha", "new_text": "start"},
                        {"old_text": "beta", "new_text": "B", "replace_all": True},
                    ],
                },
                ctx,
            )

            self.assertIn("Updated demo.txt (3 replacements) [multi-edit]", result)
            self.assertEqual(target.read_text(encoding="utf-8"), "start B gamma B\n")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_edit_file_approval_request_includes_diff_preview(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_edit_file_approval"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        target = cwd / "demo.txt"
        target.write_text("hello world\n", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            request = EditFileTool().approval_request(
                {"path": "demo.txt", "old_text": "world", "new_text": "agent"},
                ctx,
            )
            self.assertIn("Pending file changes", request.details)
            self.assertIn("files: 1", request.details)
            self.assertIn("update: 1", request.details)
            self.assertIn("[file demo.txt]", request.details)
            self.assertIn("action: update", request.details)
            self.assertIn("mode: targeted replace", request.details)
            self.assertIn("replacements: 1", request.details)
            self.assertIn("--- a/demo.txt", request.details)
            self.assertIn("+++ b/demo.txt", request.details)
            self.assertIn("-hello world", request.details)
            self.assertIn("+hello agent", request.details)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_edit_file_missing_old_text_reports_candidates_and_next_step(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_edit_file_missing_text"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        target = cwd / "demo.txt"
        target.write_text("hello world\nhello agent\n", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            with self.assertRaises(ValueError) as exc_info:
                EditFileTool().execute(
                    {"path": "demo.txt", "old_text": "hello bot", "new_text": "hello user"},
                    ctx,
                )

            message = str(exc_info.exception)
            self.assertIn("old_text was not found in the target file.", message)
            self.assertIn("Closest matching lines:", message)
            self.assertIn("hello world", message)
            self.assertIn("Next step: read the file again", message)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_edit_file_missing_file_suggests_write_file_or_create_flag(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_edit_file_missing_file"
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
            with self.assertRaises(FileNotFoundError) as exc_info:
                EditFileTool().execute(
                    {"path": "missing.txt", "old_text": "a", "new_text": "b"},
                    ctx,
                )

            message = str(exc_info.exception)
            self.assertIn("File does not exist: missing.txt", message)
            self.assertIn("create_if_missing=true", message)
            self.assertIn("write_file", message)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
