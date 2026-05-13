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
from claudecode_py.tools.apply_patch import ApplyPatchTool
from claudecode_py.tools.base import ToolContext


class ApplyPatchToolTests(unittest.TestCase):
    def test_apply_patch_adds_updates_and_deletes_files(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_apply_patch"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.txt").write_text("alpha\nbeta\ngamma", encoding="utf-8")
        (cwd / "old.txt").write_text("old", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            tool = ApplyPatchTool()
            patch = """*** Begin Patch
*** Add File: notes.txt
+hello
+world
*** Update File: demo.txt
@@
 alpha
-beta
+BETA
 gamma
*** Delete File: old.txt
*** End Patch"""

            result = tool.execute({"patch": patch}, ctx)

            self.assertIn("Created notes.txt", result)
            self.assertIn("Updated demo.txt", result)
            self.assertIn("Deleted old.txt", result)
            self.assertEqual((cwd / "notes.txt").read_text(encoding="utf-8"), "hello\nworld")
            self.assertEqual((cwd / "demo.txt").read_text(encoding="utf-8"), "alpha\nBETA\ngamma")
            self.assertFalse((cwd / "old.txt").exists())
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_apply_patch_supports_move_to(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_apply_patch_move"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "before.txt").write_text("one\ntwo", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            tool = ApplyPatchTool()
            patch = """*** Begin Patch
*** Update File: before.txt
*** Move to: after.txt
@@
-one
+ONE
 two
*** End Patch"""

            result = tool.execute({"patch": patch}, ctx)

            self.assertIn("Moved before.txt -> after.txt", result)
            self.assertFalse((cwd / "before.txt").exists())
            self.assertEqual((cwd / "after.txt").read_text(encoding="utf-8"), "ONE\ntwo")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_apply_patch_approval_request_includes_preview(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_apply_patch_approval"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.txt").write_text("alpha\nbeta\ngamma", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            patch = """*** Begin Patch
*** Update File: demo.txt
@@
alpha
-beta
+BETA
gamma
*** End Patch"""
            request = ApplyPatchTool().approval_request({"patch": patch}, ctx)
            self.assertIn("Pending file changes", request.details)
            self.assertIn("files: 1", request.details)
            self.assertIn("update: 1", request.details)
            self.assertIn("[file demo.txt]", request.details)
            self.assertIn("action: update", request.details)
            self.assertIn("--- a/demo.txt", request.details)
            self.assertIn("+++ b/demo.txt", request.details)
            self.assertIn("-beta", request.details)
            self.assertIn("+BETA", request.details)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_apply_patch_reports_detailed_hunk_match_failure(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_apply_patch_failure"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.txt").write_text("alpha\nBETA\ngamma", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            patch = """*** Begin Patch
*** Update File: demo.txt
@@
 alpha
-beta
+BETA
 gamma
*** End Patch"""

            with self.assertRaises(ValueError) as exc_info:
                ApplyPatchTool().execute({"patch": patch}, ctx)

            message = str(exc_info.exception)
            self.assertIn("Could not match patch hunk 1 in demo.txt.", message)
            self.assertIn("Expected hunk context:", message)
            self.assertIn("  beta", message)
            self.assertIn("Nearest candidate starts at line 1:", message)
            self.assertIn("First mismatch at hunk line 2", message)
            self.assertIn("'beta'", message)
            self.assertIn("'BETA'", message)
            self.assertIn("Next steps:", message)
            self.assertIn("Read the latest file contents", message)
            self.assertIn("prefer edit_file", message)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_apply_patch_approval_request_groups_multi_file_preview(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_apply_patch_multi_approval"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.txt").write_text("alpha\nbeta\ngamma", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            patch = """*** Begin Patch
*** Add File: notes.txt
+hello
*** Update File: demo.txt
@@
 alpha
-beta
+BETA
 gamma
*** End Patch"""
            request = ApplyPatchTool().approval_request({"patch": patch}, ctx)
            self.assertIn("Pending file changes", request.details)
            self.assertIn("files: 2", request.details)
            self.assertIn("create: 1", request.details)
            self.assertIn("update: 1", request.details)
            self.assertIn("[file notes.txt]", request.details)
            self.assertIn("[file demo.txt]", request.details)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
