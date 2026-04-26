from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.skills import load_project_context


class SkillsLoaderTests(unittest.TestCase):
    def test_load_project_context_reads_memory_and_skills(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_skills"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".pyclaude" / "skills").mkdir(parents=True)
        (cwd / "CLAUDE.md").write_text("Project memory text", encoding="utf-8")
        (cwd / ".pyclaude" / "skills" / "review.md").write_text(
            "---\n"
            "description: Review code changes carefully\n"
            "auto_enable: true\n"
            "tags: review,quality\n"
            "---\n\n"
            "Always review changes carefully.",
            encoding="utf-8",
        )

        try:
            context = load_project_context(cwd)
            self.assertEqual(context.memory_content, "Project memory text")
            self.assertEqual(context.skills[0].name, "review")
            self.assertEqual(context.skills[0].description, "Review code changes carefully")
            self.assertTrue(context.skills[0].auto_enable)
            self.assertEqual(context.skills[0].tags, ("review", "quality"))
            self.assertIn("Always review", context.skills[0].content)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
