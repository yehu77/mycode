from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.permissions import ApprovalRequest, ApprovalResult, PermissionDeniedError, PermissionManager
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

    def test_shell_and_dangerous_shell_have_distinct_session_scopes(self) -> None:
        manager = PermissionManager(interactive=True)
        bash_tool = BashTool()
        with patch("builtins.input", side_effect=["s", "o"]) as mocked_input:
            manager.require_approval(bash_tool.approval_request({"command": "Get-ChildItem"}))
            manager.require_approval(bash_tool.approval_request({"command": "Remove-Item demo.txt"}))

        self.assertTrue(manager.is_session_allowed("shell"))
        self.assertFalse(manager.is_session_allowed("dangerous_shell"))
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
        self.assertEqual(request.risk_level, "dangerous_shell")
        self.assertEqual(request.approval_key, "dangerous_shell")

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


if __name__ == "__main__":
    unittest.main()
