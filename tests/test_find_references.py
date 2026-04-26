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
from claudecode_py.tools.find_references import FindReferencesTool


class FindReferencesToolTests(unittest.TestCase):
    def test_find_references_excludes_obvious_definition_lines(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_references"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "def build():\n"
            "    return helper()\n\n"
            "def helper():\n"
            "    return build()\n",
            encoding="utf-8",
        )
        (cwd / "usage.py").write_text(
            "from demo import build\n\n"
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
            tool = FindReferencesTool()

            result = tool.execute({"symbol": "build", "path": "."}, ctx)

            self.assertIn("demo.py:5:return build()", result)
            self.assertIn("usage.py:1:from demo import build", result)
            self.assertIn("usage.py:3:value = build()", result)
            self.assertNotIn("demo.py:1:def build()", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_references_uses_python_ast_for_method_calls(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_references_ast"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "class Builder:\n"
            "    def build(self):\n"
            "        return 1\n\n"
            "    def run(self):\n"
            "        return self.build()\n\n"
            "value = Builder().build()\n"
            "build = 3\n",
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
            tool = FindReferencesTool()

            result = tool.execute({"symbol": "build", "path": "."}, ctx)

            self.assertIn("demo.py:6:return self.build()", result)
            self.assertIn("demo.py:8:value = Builder().build()", result)
            self.assertNotIn("demo.py:2:def build(self):", result)
            self.assertNotIn("demo.py:9:build = 3", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_references_returns_no_references_when_missing(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_references_missing"
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
            tool = FindReferencesTool()

            result = tool.execute({"symbol": "missing_name", "path": "."}, ctx)

            self.assertEqual(result, "No references found.")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_references_supports_js_ts(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_references_ts"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.ts").write_text(
            "export function build() {\n"
            "  return 1;\n"
            "}\n",
            encoding="utf-8",
        )
        (cwd / "usage.ts").write_text(
            "import { build } from './demo';\n"
            "const value = build();\n",
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
            tool = FindReferencesTool()

            result = tool.execute({"symbol": "build", "path": ".", "scope": "workspace"}, ctx)

            self.assertIn("usage.ts:1:import { build } from './demo';", result)
            self.assertIn("usage.ts:2:const value = build();", result)
            self.assertNotIn("demo.ts:1:export function build()", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_references_honors_current_file_scope(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_references_scope_file"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "def build():\n"
            "    return 1\n\n"
            "value = build()\n",
            encoding="utf-8",
        )
        (cwd / "usage.py").write_text(
            "from demo import build\n"
            "other = build()\n",
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
            tool = FindReferencesTool()

            result = tool.execute(
                {"symbol": "build", "path": "demo.py", "scope": "current_file"},
                ctx,
            )

            self.assertIn("demo.py:4:value = build()", result)
            self.assertNotIn("usage.py:", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_references_honors_workspace_scope(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_references_scope_workspace"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        src = cwd / "src"
        src.mkdir()
        (src / "demo.py").write_text(
            "def build():\n"
            "    return 1\n\n"
            "value = build()\n",
            encoding="utf-8",
        )
        (cwd / "usage.py").write_text(
            "from src.demo import build\n"
            "other = build()\n",
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
            tool = FindReferencesTool()

            result = tool.execute(
                {"symbol": "build", "path": "src", "scope": "workspace"},
                ctx,
            )

            self.assertIn("src/demo.py:4:value = build()", result)
            self.assertIn("usage.py:1:from src.demo import build", result)
            self.assertIn("usage.py:2:other = build()", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_references_filters_unrelated_attribute_names(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_references_precision"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "class Builder:\n"
            "    def build(self):\n"
            "        return 1\n\n"
            "class House:\n"
            "    def __init__(self):\n"
            "        self.build = 'brick'\n\n"
            "builder = Builder()\n"
            "house = House()\n"
            "first = builder.build()\n"
            "second = house.build\n",
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
            tool = FindReferencesTool()

            result = tool.execute({"symbol": "build", "path": "."}, ctx)

            self.assertIn("demo.py:11:first = builder.build()", result)
            self.assertNotIn("demo.py:11:second = house.build", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_references_tracks_self_attribute_instance_types(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_references_self_attr"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "class Builder:\n"
            "    def build(self):\n"
            "        return 1\n\n"
            "class Runner:\n"
            "    def __init__(self):\n"
            "        self.helper = Builder()\n\n"
            "    def run(self):\n"
            "        return self.helper.build()\n",
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
            tool = FindReferencesTool()

            result = tool.execute({"symbol": "build", "path": "."}, ctx)

            self.assertIn("demo.py:10:return self.helper.build()", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_references_tracks_import_alias_instance_types(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_references_import_alias"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        pkg = cwd / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "demo.py").write_text(
            "class Builder:\n"
            "    def build(self):\n"
            "        return 1\n",
            encoding="utf-8",
        )
        (cwd / "usage.py").write_text(
            "from pkg.demo import Builder as B\n\n"
            "builder = B()\n"
            "value = builder.build()\n",
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
            tool = FindReferencesTool()

            result = tool.execute({"symbol": "build", "path": ".", "scope": "workspace"}, ctx)

            self.assertIn("usage.py:4:value = builder.build()", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_references_tracks_parameter_annotations(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_references_param_annotations"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "class Builder:\n"
            "    def build(self):\n"
            "        return 1\n\n"
            "def run(builder: Builder):\n"
            "    return builder.build()\n",
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
            tool = FindReferencesTool()

            result = tool.execute({"symbol": "build", "path": "."}, ctx)

            self.assertIn("demo.py:6:return builder.build()", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_references_tracks_annotated_alias_variables(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_references_annotated_alias"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        pkg = cwd / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "demo.py").write_text(
            "class Builder:\n"
            "    def build(self):\n"
            "        return 1\n",
            encoding="utf-8",
        )
        (cwd / "usage.py").write_text(
            "from pkg.demo import Builder as B\n\n"
            "builder: B\n"
            "value = builder.build()\n",
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
            tool = FindReferencesTool()

            result = tool.execute({"symbol": "build", "path": ".", "scope": "workspace"}, ctx)

            self.assertIn("usage.py:4:value = builder.build()", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_find_references_refreshes_index_after_method_definition_changes(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_find_references_refresh"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        target = cwd / "demo.py"
        target.write_text(
            "class Builder:\n"
            "    pass\n\n"
            "class House:\n"
            "    def __init__(self):\n"
            "        self.deploy = 'brick'\n\n"
            "builder = Builder()\n"
            "house = House()\n"
            "good = builder.deploy()\n"
            "bad = house.deploy\n",
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
            tool = FindReferencesTool()

            before = tool.execute({"symbol": "deploy", "path": "."}, ctx)
            self.assertIn("demo.py:10:good = builder.deploy()", before)
            self.assertIn("demo.py:11:bad = house.deploy", before)

            target.write_text(
                "class Builder:\n"
                "    def deploy(self):\n"
                    "        return 1\n\n"
                "class House:\n"
                "    def __init__(self):\n"
                "        self.deploy = 'brick'\n\n"
                "builder = Builder()\n"
                "house = House()\n"
                "good = builder.deploy()\n"
                "bad = house.deploy\n",
                encoding="utf-8",
            )

            result = tool.execute({"symbol": "deploy", "path": "."}, ctx)

            self.assertIn("demo.py:11:good = builder.deploy()", result)
            self.assertNotIn("demo.py:11:bad = house.deploy", result)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
