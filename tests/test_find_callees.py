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
from claudecode_py.tools.find_callees import FindCalleesTool


class FindCalleesToolTests(unittest.TestCase):
    def test_find_callees_reports_python_callees(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_callees"
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
            tool = FindCalleesTool()

            result = tool.execute({"symbol": "build", "path": "."}, ctx)

            self.assertIn("Callees for build", result)
            self.assertIn("- demo.py:4:def build", result)
            self.assertIn("5:helper", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_callees_reports_missing_definition(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_callees_missing"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text("value = 1\n", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            tool = FindCalleesTool()

            result = tool.execute({"symbol": "missing_name", "path": "."}, ctx)

            self.assertEqual(result, 'No function or method definitions found for "missing_name" in the project indexes.')
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_callees_reports_js_ts_callees(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_callees_ts"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.ts").write_text(
            "export function boot() {\n"
            "  return 1;\n"
            "}\n\n"
            "export const run = () => boot();\n",
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
            tool = FindCalleesTool()

            result = tool.execute({"symbol": "run", "path": "."}, ctx)

            self.assertIn("Callees for run", result)
            self.assertIn("- demo.ts:5:const => run", result)
            self.assertIn("5:boot", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
