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
from claudecode_py.tools.find_symbol import FindSymbolTool


class FindSymbolToolTests(unittest.TestCase):
    def test_find_symbol_finds_python_definitions_and_methods(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_symbol"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "class Worker:\n"
            "    def build(self):\n"
            "        return 1\n\n"
            "def build():\n"
            "    return Worker()\n",
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
            tool = FindSymbolTool()

            result = tool.execute({"symbol": "build", "path": "."}, ctx)

            self.assertIn("demo.py:2:Worker.def build", result)
            self.assertIn("demo.py:5:def build", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_symbol_has_generic_fallback(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_symbol_generic"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.ts").write_text(
            "export function build() {}\n"
            "export class Builder {}\n",
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
            tool = FindSymbolTool()

            result = tool.execute({"symbol": "build", "path": "."}, ctx)

            self.assertIn("demo.ts:1:function build", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_symbol_uses_js_ts_project_index(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_symbol_ts"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "ui.tsx").write_text(
            "export class App {\n"
            "  render() {\n"
            "    return null;\n"
            "  }\n"
            "}\n"
            "export const boot = () => 1;\n",
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
            tool = FindSymbolTool()

            self.assertIn("ui.tsx:6:const => boot", tool.execute({"symbol": "boot", "path": "."}, ctx))
            self.assertIn("ui.tsx:2:App.method render", tool.execute({"symbol": "render", "path": "."}, ctx))
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_symbol_refreshes_project_index_after_file_changes(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_symbol_refresh"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        target = cwd / "demo.py"
        target.write_text(
            "def build():\n"
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
            tool = FindSymbolTool()

            missing = tool.execute({"symbol": "deploy", "path": "."}, ctx)
            self.assertEqual(missing, "No symbol definitions found.")

            target.write_text(
                "def build():\n"
                "    return 1\n\n"
                "def deploy():\n"
                "    return 2\n",
                encoding="utf-8",
            )

            result = tool.execute({"symbol": "deploy", "path": "."}, ctx)

            self.assertIn("demo.py:4:def deploy", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
