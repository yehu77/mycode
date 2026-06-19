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

    def test_load_project_context_prefers_directory_skills_and_parses_richer_frontmatter(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_directory_skills"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".claude" / "skills" / "ship").mkdir(parents=True)
        (cwd / ".pyclaude" / "skills").mkdir(parents=True)
        (cwd / ".claude" / "skills" / "ship" / "SKILL.md").write_text(
            "---\n"
            "description: Ship a release\n"
            "when_to_use: Use when a release needs to be cut\n"
            "argument-hint: <version> [notes]\n"
            "arguments:\n"
            "  - version\n"
            "  - notes\n"
            "allowed-tools:\n"
            "  - Read\n"
            "  - Edit\n"
            "  - Bash(git status:*, git tag:*)\n"
            "context: fork\n"
            "---\n\n"
            "Release version: $version\n"
            "Notes: ${notes}\n"
            "Skill root: ${CLAUDE_SKILL_DIR}\n",
            encoding="utf-8",
        )
        (cwd / ".pyclaude" / "skills" / "ship.md").write_text(
            "Legacy ship skill.",
            encoding="utf-8",
        )

        try:
            context = load_project_context(cwd)
            self.assertEqual(len(context.skills), 1)
            skill = context.skills[0]
            self.assertEqual(skill.name, "ship")
            self.assertEqual(skill.description, "Ship a release")
            self.assertEqual(skill.when_to_use, "Use when a release needs to be cut")
            self.assertTrue(skill.user_invocable)
            self.assertEqual(skill.argument_hint, "<version> [notes]")
            self.assertEqual(skill.arguments, ("version", "notes"))
            self.assertEqual(skill.execution_context, "fork")
            self.assertIn("read_file", skill.allowed_tool_names)
            self.assertIn("edit_file", skill.allowed_tool_names)
            self.assertIn("apply_patch", skill.allowed_tool_names)
            self.assertIn("bash", skill.allowed_tool_names)
            self.assertEqual(skill.allowed_bash_command_prefixes, ("git status", "git tag"))
            self.assertEqual(skill.skill_root, cwd / ".claude" / "skills" / "ship")
            self.assertEqual(len(context.skill_diagnostics), 1)
            self.assertIn("Skill name conflict", context.skill_diagnostics[0].error)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_load_project_context_parses_model_invocation_controls(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_skill_model_controls"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".claude" / "skills" / "ship").mkdir(parents=True)
        (cwd / ".claude" / "skills" / "ship" / "SKILL.md").write_text(
            "---\n"
            "description: Ship a release\n"
            "user-invocable: true\n"
            "disable-model-invocation: true\n"
            "model: claude-opus-4-6\n"
            "effort: high\n"
            "---\n\n"
            "Ship it.\n",
            encoding="utf-8",
        )

        try:
            context = load_project_context(cwd)
            skill = context.skills[0]
            self.assertTrue(skill.user_invocable)
            self.assertTrue(skill.disable_model_invocation)
            self.assertEqual(skill.model, "claude-opus-4-6")
            self.assertEqual(skill.effort, "high")
            self.assertEqual(context.skill_diagnostics, [])
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_invalid_skill_effort_creates_diagnostic_without_failing_load(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_skill_invalid_effort"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".claude" / "skills" / "ship").mkdir(parents=True)
        (cwd / ".claude" / "skills" / "ship" / "SKILL.md").write_text(
            "---\n"
            "description: Ship a release\n"
            "effort: turbo\n"
            "---\n\n"
            "Ship it.\n",
            encoding="utf-8",
        )

        try:
            context = load_project_context(cwd)
            skill = context.skills[0]
            self.assertEqual(skill.effort, "")
            self.assertEqual(len(context.skill_diagnostics), 1)
            self.assertIn("Unsupported skill effort", context.skill_diagnostics[0].error)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
