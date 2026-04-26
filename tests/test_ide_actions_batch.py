from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.runtime.headless import reference_targets_headless, symbol_actions_headless
from claudecode_py.session import Session


class IdeActionBatchTests(unittest.TestCase):
    def test_session_builds_reference_targets(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_ide_actions_reference_targets"
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
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            result = session.build_reference_targets("build", scope="workspace")
            self.assertEqual(result.symbol, "build")
            self.assertEqual(result.targets[0].action, "open_reference")
            self.assertEqual(result.targets[0].path, "demo.py")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_headless_reference_targets_returns_envelope(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_ide_actions_reference_targets_headless"
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
            result = reference_targets_headless(
                "build",
                config=SessionConfig(cwd=cwd, interactive=False),
                scope="workspace",
            )
            payload = result.to_dict()
            self.assertEqual(payload["kind"], "reference_targets")
            self.assertEqual(payload["payload"]["symbol"], "build")
            self.assertEqual(payload["payload"]["targets"][0]["path"], "demo.py")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_session_builds_symbol_action_bundle(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_ide_actions_symbol_bundle"
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
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            result = session.build_symbol_action_bundle("build")
            self.assertEqual(result.symbol, "build")
            self.assertEqual(result.definitions[0].action, "open_symbol")
            self.assertEqual(result.references[0].action, "open_reference")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_headless_symbol_actions_returns_envelope(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_ide_actions_symbol_bundle_headless"
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
            result = symbol_actions_headless(
                "build",
                config=SessionConfig(cwd=cwd, interactive=False),
            )
            payload = result.to_dict()
            self.assertEqual(payload["kind"], "symbol_actions")
            self.assertEqual(payload["payload"]["symbol"], "build")
            self.assertEqual(payload["payload"]["definitions"][0]["action"], "open_symbol")
            self.assertEqual(payload["payload"]["references"][0]["action"], "open_reference")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)
