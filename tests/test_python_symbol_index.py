from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.indexing import build_python_project_index


class PythonProjectIndexTests(unittest.TestCase):
    def test_builds_project_wide_symbol_index(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_python_symbol_index"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / "pkg").mkdir(parents=True)
        (cwd / "pkg" / "demo.py").write_text(
            "class Worker:\n"
            "    def build(self):\n"
            "        return 1\n\n"
            "def helper():\n"
            "    return Worker()\n",
            encoding="utf-8",
        )

        try:
            index = build_python_project_index(cwd)
            build_matches = [item.render() for item in index.find("build")]
            helper_matches = [item.render() for item in index.find("helper")]

            self.assertEqual(index.indexed_files, 1)
            self.assertIn("pkg/demo.py:2:Worker.def build", build_matches)
            self.assertIn("pkg/demo.py:5:def helper", helper_matches)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_builds_project_wide_import_index(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_python_import_index"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / "pkg").mkdir(parents=True)
        (cwd / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (cwd / "pkg" / "demo.py").write_text(
            "from pkg.util import helper\n"
            "import pkg.api\n\n"
            "def build():\n"
            "    return helper()\n",
            encoding="utf-8",
        )
        (cwd / "pkg" / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

        try:
            index = build_python_project_index(cwd)

            self.assertEqual(len(index.imports), 2)
            rendered = [item.render() for item in index.imports_for_path("pkg/demo.py")]
            self.assertIn("pkg/demo.py:1:from pkg.util import helper", rendered)
            self.assertIn("pkg/demo.py:2:import pkg.api", rendered)
            importers = [item.rel_path for item in index.importers_for_module("pkg.util")]
            self.assertIn("pkg/demo.py", importers)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_builds_project_wide_inheritance_index(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_python_inheritance_index"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / "pkg").mkdir(parents=True)
        (cwd / "pkg" / "demo.py").write_text(
            "class Base:\n"
            "    pass\n\n"
            "class Worker(Base):\n"
            "    pass\n\n"
            "class Advanced(Worker, Mixin):\n"
            "    pass\n",
            encoding="utf-8",
        )

        try:
            index = build_python_project_index(cwd)

            self.assertEqual(len(index.inheritances), 3)
            worker = index.inheritance_for_class("Worker")[0]
            self.assertEqual(worker.bases, ("Base",))
            derived = {item.class_name for item in index.derived_classes("Worker")}
            self.assertIn("Advanced", derived)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_builds_project_wide_call_index(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_python_call_index"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / "pkg").mkdir(parents=True)
        (cwd / "pkg" / "demo.py").write_text(
            "def helper():\n"
            "    return 1\n\n"
            "class Worker:\n"
            "    def build(self):\n"
            "        return helper()\n\n"
            "def run():\n"
            "    worker = Worker()\n"
            "    return worker.build()\n",
            encoding="utf-8",
        )

        try:
            index = build_python_project_index(cwd)

            helper_callers = [item.render() for item in index.calls_for_callee("helper")]
            build_callers = [item.render() for item in index.calls_for_callee("build")]
            run_calls = [item.render() for item in index.calls_from_caller("run")]

            self.assertIn("pkg/demo.py:6:Worker.build -> helper", helper_callers)
            self.assertIn("pkg/demo.py:10:run -> build", build_callers)
            self.assertIn("pkg/demo.py:9:run -> Worker", run_calls)
            self.assertIn("pkg/demo.py:10:run -> build", run_calls)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)
