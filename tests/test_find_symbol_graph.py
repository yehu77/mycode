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
from claudecode_py.tools.find_symbol_graph import FindSymbolGraphTool


class FindSymbolGraphToolTests(unittest.TestCase):
    def test_find_symbol_graph_reports_definitions_and_references(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_symbol_graph"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "class Builder:\n"
            "    def build(self):\n"
            "        return 1\n\n"
            "def build():\n"
            "    return Builder()\n",
            encoding="utf-8",
        )
        (cwd / "usage.py").write_text(
            "from demo import build\n"
            "import demo\n\n"
            "value = build()\n",
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
            tool = FindSymbolGraphTool()

            result = tool.execute({"symbol": "build", "path": ".", "scope": "workspace"}, ctx)

            self.assertIn("Symbol graph for build", result)
            self.assertIn("summary:", result)
            self.assertIn("- definitions=2", result)
            self.assertIn("definitions:", result)
            self.assertIn("demo.py:2:Builder.def build", result)
            self.assertIn("demo.py:5:def build", result)
            self.assertIn("references:", result)
            self.assertIn("- usage.py", result)
            self.assertIn("1:from demo import build", result)
            self.assertIn("4:value = build()", result)
            self.assertIn("imports:", result)
            self.assertIn("- demo.py", result)
            self.assertIn("  - imported_by: usage.py", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_symbol_graph_reports_empty_result(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_symbol_graph_empty"
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
            tool = FindSymbolGraphTool()

            result = tool.execute({"symbol": "missing_symbol", "path": "."}, ctx)

            self.assertEqual(result, 'No definitions or references found for "missing_symbol".')
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_symbol_graph_reports_js_ts_definitions_and_references(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_symbol_graph_ts"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.ts").write_text(
            "import { mount } from './shared';\n"
            "export class App {\n"
            "  render() {\n"
            "    return mount();\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        (cwd / "shared.ts").write_text(
            "export function mount() {\n"
            "  return 1;\n"
            "}\n",
            encoding="utf-8",
        )
        (cwd / "usage.ts").write_text(
            "const app = new App();\n"
            "app.render();\n",
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
            tool = FindSymbolGraphTool()

            result = tool.execute({"symbol": "render", "path": ".", "scope": "workspace"}, ctx)

            self.assertIn("Symbol graph for render", result)
            self.assertIn("demo.ts:3:App.method render", result)
            self.assertIn("- usage.ts", result)
            self.assertIn("2:app.render();", result)
            self.assertIn("imports:", result)
            self.assertIn("- demo.ts", result)
            self.assertIn("  - imports: shared", result)
            self.assertIn("calls:", result)
            self.assertIn("calls: mount (demo.ts:4)", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_symbol_graph_reports_python_inheritance_graph(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_symbol_graph_inheritance"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "class Base:\n"
            "    pass\n\n"
            "class Worker(Base):\n"
            "    pass\n\n"
            "class Advanced(Worker):\n"
            "    pass\n",
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
            tool = FindSymbolGraphTool()

            result = tool.execute({"symbol": "Worker", "path": ".", "scope": "workspace"}, ctx)

            self.assertIn("inheritance:", result)
            self.assertIn("- demo.py", result)
            self.assertIn("  - bases: Base", result)
            self.assertIn("derived_by: Advanced (demo.py:7)", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_symbol_graph_reports_python_call_graph(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_symbol_graph_calls"
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
            tool = FindSymbolGraphTool()

            result = tool.execute({"symbol": "build", "path": ".", "scope": "workspace"}, ctx)

            self.assertIn("calls:", result)
            self.assertIn("called_by: run (demo.py:8)", result)
            self.assertIn("calls: helper (demo.py:5)", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)
