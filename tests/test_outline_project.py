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
from claudecode_py.tools.outline_project import OutlineProjectTool


class OutlineProjectToolTests(unittest.TestCase):
    def test_outline_project_summarizes_indexed_python_files(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_outline_project"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / "pkg").mkdir(parents=True)
        (cwd / "pkg" / "demo.py").write_text(
            "from pkg.util import deploy\n\n"
            "class Base:\n"
            "    pass\n\n"
            "class Worker(Base):\n"
            "    def build(self):\n"
            "        return 1\n\n"
            "def helper():\n"
            "    return deploy() + Worker().build()\n",
            encoding="utf-8",
        )
        (cwd / "pkg" / "util.py").write_text(
            "def deploy():\n"
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
            tool = OutlineProjectTool()

            result = tool.execute({"path": "pkg"}, ctx)

            self.assertIn("Project outline for pkg", result)
            self.assertIn("indexed_files: 2", result)
            self.assertIn("indexed_symbols: 5", result)
            self.assertIn("indexed_python_files: 2", result)
            self.assertIn("indexed_python_symbols: 5", result)
            self.assertIn("indexed_python_imports: 1", result)
            self.assertIn("indexed_python_inheritances: 2", result)
            self.assertIn("indexed_python_calls: 3", result)
            self.assertIn("- pkg/demo.py", result)
            self.assertIn("class Base", result)
            self.assertIn("class Worker", result)
            self.assertIn("Worker.def build", result)
            self.assertIn("def helper", result)
            self.assertIn("imports: pkg.util", result)
            self.assertIn("inherits: Worker <- Base", result)
            self.assertIn("calls: Worker, build, deploy", result)
            self.assertIn("- pkg/util.py", result)
            self.assertIn("def deploy", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_outline_project_summarizes_indexed_js_ts_files(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_outline_project_ts"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "ui.ts").write_text(
            "import { bootHelper } from './util';\n"
            "export class App {\n"
            "  render() {\n"
            "    return bootHelper();\n"
            "  }\n"
            "}\n"
            "export const boot = () => 1;\n",
            encoding="utf-8",
        )
        (cwd / "util.ts").write_text(
            "export function bootHelper() {\n"
            "  return 1;\n"
            "}\n",
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
            tool = OutlineProjectTool()

            result = tool.execute({"path": "."}, ctx)

            self.assertIn("indexed_files: 2", result)
            self.assertIn("indexed_symbols: 4", result)
            self.assertIn("indexed_js_ts_files: 2", result)
            self.assertIn("indexed_js_ts_symbols: 4", result)
            self.assertIn("indexed_js_ts_imports: 1", result)
            self.assertIn("indexed_js_ts_calls: 1", result)
            self.assertIn("- ui.ts", result)
            self.assertIn("class App", result)
            self.assertIn("App.method render", result)
            self.assertIn("const => boot", result)
            self.assertIn("imports: util", result)
            self.assertIn("calls: bootHelper", result)
            self.assertIn("- util.ts", result)
            self.assertIn("function bootHelper", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_outline_project_reports_empty_scope(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_outline_project_empty"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.txt").write_text("hello", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            tool = OutlineProjectTool()

            result = tool.execute({"path": "."}, ctx)

            self.assertIn("No indexed project symbols found", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)
