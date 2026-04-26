from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.permissions import PermissionManager
from claudecode_py.session import Session
from claudecode_py.tasks import TaskManager
from claudecode_py.tools.base import ToolContext
from claudecode_py.tools.outline_file import OutlineFileTool


class OutlineFileToolTests(unittest.TestCase):
    def test_outline_file_summarizes_python_symbols(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_outline_file"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        target = cwd / "demo.py"
        target.write_text(
            "VALUE = 3\n\n"
            "class Demo:\n"
            "    def method(self):\n"
            "        return VALUE\n\n"
            "async def run():\n"
            "    return 1\n",
            encoding="utf-8",
        )

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            tool = OutlineFileTool()

            result = tool.execute({"path": "demo.py"}, ctx)

            self.assertIn("Outline for demo.py", result)
            self.assertIn("language: python", result)
            self.assertIn("class Demo", result)
            self.assertIn("def method()", result)
            self.assertIn("async def run()", result)
            self.assertIn("VALUE", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_outline_file_has_generic_fallback(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_outline_generic"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        target = cwd / "demo.ts"
        target.write_text(
            "export class Widget {}\n"
            "export function build() {}\n",
            encoding="utf-8",
        )

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            tool = OutlineFileTool()

            result = tool.execute({"path": "demo.ts"}, ctx)

            self.assertIn("language: generic", result)
            self.assertIn("export class Widget {}", result)
            self.assertIn("export function build() {}", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
