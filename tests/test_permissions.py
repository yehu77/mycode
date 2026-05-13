from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.permissions import (
    ApprovalRequest,
    ApprovalResult,
    PermissionDecision,
    PermissionDeniedError,
    PermissionManager,
    PermissionRule,
    PermissionRuleScope,
)
from claudecode_py.tools.bash import BashTool


class PermissionTests(unittest.TestCase):
    def test_read_only_tool_skips_prompt(self) -> None:
        manager = PermissionManager(interactive=True)
        with patch("builtins.input") as mocked_input:
            manager.require_approval(
                ApprovalRequest(
                    tool_name="read_file",
                    reason="Read a file",
                    risk_level="read",
                )
            )
        mocked_input.assert_not_called()

    def test_allow_once_does_not_persist_for_same_risk(self) -> None:
        manager = PermissionManager(interactive=True)
        with patch("builtins.input", side_effect=["o", "o"]) as mocked_input:
            manager.require_approval(
                ApprovalRequest(
                    tool_name="edit_file",
                    reason="Edit a file",
                    risk_level="write",
                    approval_key="write",
                )
            )
            manager.require_approval(
                ApprovalRequest(
                    tool_name="write_file",
                    reason="Write a file",
                    risk_level="write",
                    approval_key="write",
                )
            )

        self.assertFalse(manager.is_session_allowed("write"))
        self.assertEqual(mocked_input.call_count, 2)

    def test_allow_session_persists_for_same_risk_group(self) -> None:
        manager = PermissionManager(interactive=True)
        with patch("builtins.input", return_value="s") as mocked_input:
            manager.require_approval(
                ApprovalRequest(
                    tool_name="write_file",
                    reason="Write a file",
                    risk_level="write",
                    approval_key="write",
                )
            )
            manager.require_approval(
                ApprovalRequest(
                    tool_name="edit_file",
                    reason="Edit a file",
                    risk_level="write",
                    approval_key="write",
                )
            )

        self.assertTrue(manager.is_session_allowed("write"))
        self.assertEqual(mocked_input.call_count, 1)

    def test_shell_write_and_shell_dangerous_have_distinct_session_scopes(self) -> None:
        manager = PermissionManager(interactive=True)
        bash_tool = BashTool()
        with patch("builtins.input", side_effect=["s", "o"]) as mocked_input:
            manager.require_approval(bash_tool.approval_request({"command": "Set-Content demo.txt hello"}))
            manager.require_approval(bash_tool.approval_request({"command": "Remove-Item demo.txt"}))

        self.assertTrue(manager.is_session_allowed("shell_write"))
        self.assertFalse(manager.is_session_allowed("shell_dangerous"))
        self.assertEqual(mocked_input.call_count, 2)

    def test_deny_raises(self) -> None:
        manager = PermissionManager(interactive=True)
        with patch("builtins.input", return_value="n"):
            with self.assertRaises(PermissionDeniedError):
                manager.require_approval(
                    ApprovalRequest(
                        tool_name="write_file",
                        reason="Write file",
                        risk_level="write",
                        approval_key="write",
                    )
                )

    def test_non_interactive_write_tool_is_denied(self) -> None:
        manager = PermissionManager(interactive=False)
        with self.assertRaises(PermissionDeniedError):
            manager.require_approval(
                ApprovalRequest(
                    tool_name="edit_file",
                    reason="Edit file",
                    risk_level="write",
                    approval_key="write",
                )
            )

    def test_custom_approval_handler_is_used(self) -> None:
        seen_requests: list[ApprovalRequest] = []

        def handler(request: ApprovalRequest):
            seen_requests.append(request)
            return ApprovalResult(decision="allow", scope="session")

        manager = PermissionManager(interactive=True, approval_handler=handler)
        manager.require_approval(
            ApprovalRequest(
                tool_name="write_file",
                reason="Write file",
                risk_level="write",
                approval_key="write",
            )
        )

        self.assertEqual(len(seen_requests), 1)
        self.assertTrue(manager.is_session_allowed("write"))

    def test_bash_classifies_dangerous_command(self) -> None:
        request = BashTool().approval_request({"command": "git reset --hard HEAD"})
        self.assertEqual(request.risk_level, "shell_dangerous")
        self.assertEqual(request.approval_key, "shell_dangerous")

    def test_permission_rules_prioritize_deny_over_allow(self) -> None:
        manager = PermissionManager(interactive=False)
        manager.extend_rules(
            [
                PermissionRule(
                    decision=PermissionDecision.ALLOW,
                    scope=PermissionRuleScope.TOOL,
                    value="edit_file",
                ),
                PermissionRule(
                    decision=PermissionDecision.DENY,
                    scope=PermissionRuleScope.PATH,
                    value="secrets",
                ),
            ]
        )

        decision = manager.evaluate(
            ApprovalRequest(
                tool_name="edit_file",
                reason="Edit file",
                risk_level="write",
                target_paths=("secrets/config.json",),
            )
        )

        self.assertEqual(decision.decision, PermissionDecision.DENY)
        self.assertTrue(any(match.startswith("deny:path:secrets") for match in decision.matched_rules))

    def test_permission_rules_allow_matching_shell_prefix(self) -> None:
        manager = PermissionManager(interactive=False)
        manager.add_rule(
            PermissionRule(
                decision=PermissionDecision.ALLOW,
                scope=PermissionRuleScope.SHELL,
                value="git status",
            )
        )

        decision = manager.evaluate(
            ApprovalRequest(
                tool_name="bash",
                reason="Inspect status",
                risk_level="shell_write",
                command="git status --short",
            )
        )

        self.assertEqual(decision.decision, PermissionDecision.ALLOW)

    def test_permission_rules_match_shell_prefix_against_any_segment(self) -> None:
        manager = PermissionManager(interactive=False)
        manager.add_rule(
            PermissionRule(
                decision=PermissionDecision.ASK,
                scope=PermissionRuleScope.SHELL,
                value="set-content",
            )
        )

        decision = manager.evaluate(
            ApprovalRequest(
                tool_name="bash",
                reason="Pipe read output into file write",
                risk_level="shell_write",
                command="Get-Content demo.txt | Set-Content out.txt",
                command_segments=("Get-Content demo.txt", "Set-Content out.txt"),
            )
        )

        self.assertEqual(decision.decision, PermissionDecision.ASK)
        self.assertTrue(any("segment 2: Set-Content out.txt" in match for match in decision.matched_rules))

    def test_permission_rules_ask_for_matching_tool(self) -> None:
        manager = PermissionManager(interactive=False)
        manager.add_rule(
            PermissionRule(
                decision=PermissionDecision.ASK,
                scope=PermissionRuleScope.TOOL,
                value="write_file",
            )
        )

        decision = manager.evaluate(
            ApprovalRequest(
                tool_name="write_file",
                reason="Write file",
                risk_level="write",
            )
        )

        self.assertEqual(decision.decision, PermissionDecision.ASK)

    def test_prompt_format_includes_multiline_details(self) -> None:
        manager = PermissionManager(interactive=True)
        prompts: list[str] = []

        def fake_input(prompt: str) -> str:
            prompts.append(prompt)
            return "o"

        with patch("builtins.input", side_effect=fake_input):
            manager.require_approval(
                ApprovalRequest(
                    tool_name="edit_file",
                    reason="Edit a file",
                    risk_level="write",
                    approval_key="write",
                    details="path=demo.txt\n--- a/demo.txt\n+++ b/demo.txt",
                )
            )

        self.assertIn("\npath=demo.txt\n--- a/demo.txt\n+++ b/demo.txt\n[o]nce/[s]ession/[n]o: ", prompts[0])

    def test_prompt_format_includes_shell_segment_match_reason(self) -> None:
        manager = PermissionManager(interactive=True)
        prompts: list[str] = []

        def fake_input(prompt: str) -> str:
            prompts.append(prompt)
            return "o"

        manager.add_rule(
            PermissionRule(
                decision=PermissionDecision.ASK,
                scope=PermissionRuleScope.SHELL,
                value="set-content",
            )
        )

        with patch("builtins.input", side_effect=fake_input):
            manager.require_approval(
                ApprovalRequest(
                    tool_name="bash",
                    reason="Run a shell command in the workspace.",
                    risk_level="shell_write",
                    approval_key="shell_write",
                    command="Get-Content demo.txt | Set-Content out.txt",
                    command_segments=("Get-Content demo.txt", "Set-Content out.txt"),
                    details="Pending shell command",
                )
            )

        self.assertIn("Matched ask rules: ask:shell:set-content [segment 2: Set-Content out.txt]", prompts[0])
        self.assertIn("matched rules: ask:shell:set-content [segment 2: Set-Content out.txt]", prompts[0])

    def test_prompt_format_includes_command_mode_section(self) -> None:
        manager = PermissionManager(interactive=True)
        prompts: list[str] = []

        def fake_input(prompt: str) -> str:
            prompts.append(prompt)
            return "o"

        with patch("builtins.input", side_effect=fake_input):
            manager.require_approval(
                ApprovalRequest(
                    tool_name="bash",
                    reason="Run a shell command in the workspace.",
                    risk_level="shell_write",
                    approval_key="shell_write",
                    command="git diff --stat",
                    details="Pending shell command",
                    command_mode_name="review",
                    command_mode_source="repl:/review",
                    command_mode_allowed_prefixes=("git diff", "git show"),
                )
            )

        self.assertIn("command_mode:", prompts[0])
        self.assertIn("- mode: review", prompts[0])
        self.assertIn("- source: repl:/review", prompts[0])
        self.assertIn("- allowed_prefixes: git diff, git show", prompts[0])

    def test_prompt_format_includes_command_mode_complex_features(self) -> None:
        manager = PermissionManager(interactive=True)
        prompts: list[str] = []

        def fake_input(prompt: str) -> str:
            prompts.append(prompt)
            return "o"

        with patch("builtins.input", side_effect=fake_input):
            manager.require_approval(
                ApprovalRequest(
                    tool_name="bash",
                    reason="Run a shell command in the workspace.",
                    risk_level="shell_write",
                    approval_key="shell_write",
                    command="git diff $(pwd)",
                    details="Pending shell command",
                    command_mode_name="review",
                    command_mode_allowed_prefixes=("git diff", "git show"),
                    command_mode_violating_segment="git diff $(pwd)",
                    command_mode_violating_segment_index=1,
                    command_mode_complex_features=("command_substitution",),
                )
            )

        self.assertIn("- complex_features: command_substitution", prompts[0])


if __name__ == "__main__":
    unittest.main()
