from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.permissions import PermissionDeniedError, PermissionManager
from claudecode_py.session import Session
from claudecode_py.tasks import TaskManager
from claudecode_py.tools.base import ToolContext
from claudecode_py.tools.bash import BashTool


class BashToolTests(unittest.TestCase):
    def test_bash_approval_request_extracts_powershell_path_arguments(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_bash_paths"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            request = BashTool().approval_request(
                {"command": "Copy-Item -Path src/demo.txt -Destination dist/demo.txt"},
                ctx,
            )

            self.assertEqual(request.risk_level, "shell_write")
            self.assertEqual(request.target_paths, ("src/demo.txt", "dist/demo.txt"))
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_bash_approval_request_extracts_redirection_target(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_bash_redirect"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            request = BashTool().approval_request(
                {"command": "git diff --stat > logs/out.txt"},
                ctx,
            )

            self.assertEqual(request.risk_level, "shell_write")
            self.assertEqual(request.target_paths, ("logs/out.txt",))
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_bash_approval_request_renders_segment_level_summary(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_bash_segments"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            request = BashTool().approval_request(
                {"command": "Get-Content demo.txt | Set-Content out.txt"},
                ctx,
            )

            self.assertEqual(request.risk_level, "shell_write")
            self.assertIn("segments: 2", request.details)
            self.assertIn("[segment 1]", request.details)
            self.assertIn("command: Get-Content demo.txt", request.details)
            self.assertIn("[segment 2]", request.details)
            self.assertIn("command: Set-Content out.txt", request.details)
            self.assertIn("paths: out.txt", request.details)
            self.assertEqual(request.command_segments, ("Get-Content demo.txt", "Set-Content out.txt"))
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_bash_approval_request_includes_command_policy_context(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_bash_policy_context"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            with session._command_execution_scope(
                allowed_tool_names=("bash",),
                allowed_bash_command_prefixes=("git diff", "git show"),
                require_read_only_subagents=False,
                command_policy_name="review",
                command_policy_source="repl:/review",
            ):
                request = BashTool().approval_request({"command": "git diff --stat"}, ctx)

            self.assertIn("Command policy:", request.details)
            self.assertIn("- mode: review", request.details)
            self.assertIn("- allowed_prefixes: git diff, git show", request.details)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_bash_analysis_aggregates_dangerous_segment(self) -> None:
        analysis = BashTool().analyze_command(
            Path(__file__).resolve().parent,
            "Get-Content demo.txt ; Remove-Item demo.txt",
        )

        self.assertEqual(analysis.risk_level, "shell_dangerous")
        self.assertEqual(len(analysis.segments), 2)
        self.assertEqual(analysis.segments[0].risk_level, "shell_read")
        self.assertEqual(analysis.segments[1].risk_level, "shell_dangerous")

    def test_bash_analysis_marks_complex_write_features_as_conservative(self) -> None:
        analysis = BashTool().analyze_command(
            Path(__file__).resolve().parent,
            "Copy-Item $(Join-Path $PWD demo.txt) -Destination out.txt",
        )

        self.assertEqual(analysis.risk_level, "shell_write")
        self.assertTrue(analysis.requires_conservative_approval)
        self.assertTrue(analysis.segments[0].uncertain)
        self.assertEqual(analysis.segments[0].features, ("command_substitution",))
        self.assertIn("complex_feature=command_substitution", analysis.segments[0].uncertainty_reason)

    def test_bash_analysis_tracks_environment_assignment_and_glob_features(self) -> None:
        analysis = BashTool().analyze_command(
            Path(__file__).resolve().parent,
            "FOO=bar Copy-Item src/*.py dist/",
        )

        self.assertEqual(analysis.risk_level, "shell_write")
        self.assertTrue(analysis.requires_conservative_approval)
        self.assertIn("env_assignment", analysis.segments[0].features)
        self.assertIn("glob_pattern", analysis.segments[0].features)
        self.assertEqual(analysis.segments[0].policy_command, "Copy-Item src/*.py dist/")

    def test_bash_analysis_extracts_input_and_output_redirection_targets(self) -> None:
        analysis = BashTool().analyze_command(
            Path(__file__).resolve().parent,
            "git diff < in.txt 1> out.txt 2>> err.txt",
        )

        self.assertEqual(analysis.risk_level, "shell_write")
        self.assertEqual(analysis.target_paths, ("in.txt", "out.txt", "err.txt"))

    def test_review_policy_rejects_write_segment_even_when_prefix_matches(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        try:
            with session._command_execution_scope(
                allowed_tool_names=("bash",),
                allowed_bash_command_prefixes=("git diff", "git show"),
                require_read_only_subagents=False,
                command_policy_name="review",
                command_policy_source="repl:/review",
            ):
                result = session.evaluate_bash_command_policy("git diff --stat > out.txt")

            self.assertFalse(result.allowed)
            self.assertIn('command mode "review"', result.reason)
            self.assertIn('segment 1 "git diff --stat > out.txt"', result.reason)
            self.assertIn("shell_write action", result.reason)
        finally:
            session.close()

    def test_review_policy_rejects_complex_read_syntax_even_when_prefix_matches(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        try:
            with session._command_execution_scope(
                allowed_tool_names=("bash",),
                allowed_bash_command_prefixes=("git diff", "git show"),
                require_read_only_subagents=False,
                command_policy_name="review",
                command_policy_source="repl:/review",
            ):
                result = session.evaluate_bash_command_policy("git diff $(pwd)")

            self.assertFalse(result.allowed)
            self.assertEqual(result.violating_features, ("command_substitution",))
            self.assertIn("complex_feature=command_substitution", result.reason)
        finally:
            session.close()

    def test_commit_policy_rejects_complex_write_syntax_conservatively(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        try:
            with session._command_execution_scope(
                allowed_tool_names=("bash",),
                allowed_bash_command_prefixes=("git add", "git commit"),
                require_read_only_subagents=False,
                command_policy_name="commit",
                command_policy_source="repl:/commit",
            ):
                result = session.evaluate_bash_command_policy(
                    "git add $(Join-Path $PWD demo.txt)",
                )

            self.assertFalse(result.allowed)
            self.assertIn('command mode "commit"', result.reason)
            self.assertEqual(result.violating_features, ("command_substitution",))
            self.assertIn("complex_feature=command_substitution", result.reason)
        finally:
            session.close()

    def test_commit_policy_allows_simple_git_write_segments(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        try:
            with session._command_execution_scope(
                allowed_tool_names=("bash",),
                allowed_bash_command_prefixes=("git add", "git commit"),
                require_read_only_subagents=False,
                command_policy_name="commit",
                command_policy_source="repl:/commit",
            ):
                result = session.evaluate_bash_command_policy(
                    'git add demo.txt && git commit -m "x"',
                )

            self.assertTrue(result.allowed)
        finally:
            session.close()

    def test_bash_validate_rejects_outside_workspace_redirection(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_bash_outside_redirect"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )

            with self.assertRaises(PermissionDeniedError) as exc_info:
                BashTool()._validate_command("Get-Content demo.txt > ../outside.txt", ctx)

            self.assertIn("outside workspace", str(exc_info.exception))
            self.assertIn("../outside.txt", str(exc_info.exception))
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_bash_validate_rejects_outside_workspace_destination_flag(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_bash_outside_destination"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.txt").write_text("hello", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )

            with self.assertRaises(PermissionDeniedError) as exc_info:
                BashTool()._validate_command(
                    "Copy-Item -Path demo.txt -Destination ../outside.txt",
                    ctx,
                )

            self.assertIn("outside workspace", str(exc_info.exception))
            self.assertIn("../outside.txt", str(exc_info.exception))
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_bash_validate_rejects_outside_workspace_in_single_segment_only(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_bash_outside_segment"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )

            with self.assertRaises(PermissionDeniedError) as exc_info:
                BashTool()._validate_command(
                    "Get-Content demo.txt && Set-Content ../outside.txt done",
                    ctx,
                )

            self.assertIn("outside workspace", str(exc_info.exception))
            self.assertIn("segment 2", str(exc_info.exception))
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
