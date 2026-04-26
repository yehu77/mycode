from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.indexing import build_js_ts_project_index


class JsTsSymbolIndexTests(unittest.TestCase):
    def test_build_js_ts_project_index_collects_common_symbols(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_js_ts_index"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        source = cwd / "app.ts"
        source.write_text(
            "export class App {\n"
            "  render() {\n"
            "    return 'ok';\n"
            "  }\n"
            "}\n"
            "export function boot() {\n"
            "  return 1;\n"
            "}\n"
            "export const start = () => 2;\n",
            encoding="utf-8",
        )
        try:
            index = build_js_ts_project_index(cwd)
            rendered = [entry.render() for entry in index.entries]
            self.assertIn("app.ts:1:class App", rendered)
            self.assertIn("app.ts:2:App.method render", rendered)
            self.assertIn("app.ts:6:function boot", rendered)
            self.assertIn("app.ts:9:const => start", rendered)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_build_js_ts_project_index_collects_imports_and_calls(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_js_ts_graph_index"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        source = cwd / "app.ts"
        source.write_text(
            "import { boot } from './util';\n"
            "const run = () => boot();\n"
            "run();\n",
            encoding="utf-8",
        )
        util = cwd / "util.ts"
        util.write_text(
            "export function boot() {\n"
            "  return 1;\n"
            "}\n",
            encoding="utf-8",
        )
        try:
            index = build_js_ts_project_index(cwd)
            rendered_imports = [entry.render() for entry in index.imports]
            rendered_calls = [entry.render() for entry in index.calls]
            self.assertIn("app.ts:1:import util", rendered_imports)
            self.assertIn("app.ts:2:run -> boot", rendered_calls)
            self.assertIn("app.ts:3:run -> run", rendered_calls)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)
