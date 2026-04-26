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
from claudecode_py.tools.find_callers import FindCallersTool


class FindCallersToolTests(unittest.TestCase):
    def test_find_callers_reports_python_callers(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_callers"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "def helper():\n"
            "    return 1\n\n"
            "def build():\n"
            "    return helper()\n\n"
            "def run():\n"
            "    return build()\n",
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
            tool = FindCallersTool()

            result = tool.execute({"symbol": "build", "path": "."}, ctx)

            self.assertIn("Callers for build", result)
            self.assertIn("- demo.py", result)
            self.assertIn("8:run -> build", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_callers_reports_missing_symbol(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_callers_missing"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            tool = FindCallersTool()

            result = tool.execute({"symbol": "missing_name", "path": "."}, ctx)

            self.assertEqual(result, 'No callers found for "missing_name" in the project call graph.')
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_callers_reports_js_ts_callers(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_callers_ts"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.ts").write_text(
            "export function build() {\n"
            "  return 1;\n"
            "}\n\n"
            "export const run = () => build();\n",
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
            tool = FindCallersTool()

            result = tool.execute({"symbol": "build", "path": "."}, ctx)

            self.assertIn("Callers for build", result)
            self.assertIn("- demo.ts", result)
            self.assertIn("5:run -> build", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
