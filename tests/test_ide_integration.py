from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.runtime.headless import collect_references_headless, locate_symbol_headless
from claudecode_py.session import Session


class IdeIntegrationTests(unittest.TestCase):
    def test_session_exposes_structured_symbol_locations(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_ide_symbol"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "class Worker:\n"
            "    def build(self):\n"
            "        return 1\n",
            encoding="utf-8",
        )

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            result = session.locate_symbol("build")
            self.assertEqual(result.symbol, "build")
            self.assertEqual(result.matches[0].path, "demo.py")
            self.assertEqual(result.matches[0].owner, "Worker")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_headless_reference_lookup_returns_structured_records(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_ide_refs"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "def build():\n"
            "    return 1\n\n"
            "value = build()\n",
            encoding="utf-8",
        )

        try:
            result = collect_references_headless(
                "build",
                config=SessionConfig(cwd=cwd, interactive=False),
                path=".",
            )
            self.assertEqual(result.lookup.symbol, "build")
            self.assertEqual(result.lookup.references[0].path, "demo.py")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_headless_symbol_lookup_returns_structured_records(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_ide_headless_symbol"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text("def deploy():\n    return 1\n", encoding="utf-8")

        try:
            result = locate_symbol_headless(
                "deploy",
                config=SessionConfig(cwd=cwd, interactive=False),
            )
            self.assertEqual(result.lookup.matches[0].path, "demo.py")
            self.assertEqual(result.lookup.matches[0].line, 1)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)
