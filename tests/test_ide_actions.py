from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.integrations import build_diff_targets
from claudecode_py.runtime.headless import diff_targets_headless, open_file_target_headless, open_symbol_target_headless
from claudecode_py.session import Session


class IdeActionTests(unittest.TestCase):
    def test_session_builds_open_symbol_target(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_ide_actions_symbol"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text("def deploy():\n    return 1\n", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            target = session.build_symbol_target("deploy")
            self.assertEqual(target.action, "open_symbol")
            self.assertEqual(target.path, "demo.py")
            self.assertEqual(target.line, 1)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_build_diff_targets_creates_hunk_navigation(self) -> None:
        result = build_diff_targets(
            "demo.py",
            "line1\nline2\nline3\n",
            "line1\nline2 changed\nline3\nline4\n",
        )

        self.assertEqual(result.path, "demo.py")
        self.assertGreaterEqual(len(result.hunks), 1)
        self.assertEqual(result.hunks[0].action, "open_diff")

    def test_headless_open_file_target_returns_structured_result(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_ide_actions_open_file"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text("value = 1\n", encoding="utf-8")

        try:
            result = open_file_target_headless(
                "demo.py",
                config=SessionConfig(cwd=cwd, interactive=False),
                line=3,
                column=2,
                label="demo target",
            )
            self.assertEqual(result.target.path, "demo.py")
            self.assertEqual(result.target.line, 3)
            self.assertEqual(result.target.column, 2)
            self.assertEqual(result.target.label, "demo target")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_headless_diff_targets_returns_structured_result(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_ide_actions_diff"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            result = diff_targets_headless(
                "demo.py",
                before="line1\nline2\n",
                after="line1\nline2 changed\n",
                config=SessionConfig(cwd=cwd, interactive=False),
            )
            self.assertEqual(result.diff.path, "demo.py")
            self.assertGreaterEqual(len(result.diff.hunks), 1)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_headless_open_symbol_target_returns_structured_result(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_ide_actions_open_symbol"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text("def deploy():\n    return 1\n", encoding="utf-8")

        try:
            result = open_symbol_target_headless(
                "deploy",
                config=SessionConfig(cwd=cwd, interactive=False),
            )
            self.assertEqual(result.target.action, "open_symbol")
            self.assertEqual(result.target.path, "demo.py")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)
