from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.workspace import create_workspace_snapshot


class WorkspaceIsolationTests(unittest.TestCase):
    def test_create_workspace_snapshot_copies_workspace_without_sessions(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_workspace_snapshot"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".pyclaude" / "sessions").mkdir(parents=True)
        (cwd / ".pyclaude" / "skills").mkdir(parents=True)
        (cwd / "src").mkdir(parents=True)
        (cwd / "src" / "demo.py").write_text("print('demo')\n", encoding="utf-8")
        (cwd / ".pyclaude" / "sessions" / "old.json").write_text("{}", encoding="utf-8")
        (cwd / ".pyclaude" / "skills" / "review.md").write_text("skill", encoding="utf-8")

        try:
            snapshot = create_workspace_snapshot(cwd)

            self.assertNotEqual(snapshot, cwd)
            self.assertTrue((snapshot / "src" / "demo.py").exists())
            self.assertTrue((snapshot / ".pyclaude" / "skills" / "review.md").exists())
            self.assertFalse((snapshot / ".pyclaude" / "sessions").exists())
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
