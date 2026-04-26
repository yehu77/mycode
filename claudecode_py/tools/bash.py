from __future__ import annotations

import os
import re
import subprocess

from ..permissions import ApprovalRequest, PermissionDeniedError
from .base import BaseTool


class BashTool(BaseTool):
    name = "bash"
    description = "Run a shell command in the workspace."
    read_only = False
    concurrency_safe = False
    risk_level = "shell"
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to execute."},
            "timeout_sec": {"type": "integer", "description": "Optional timeout in seconds."},
        },
        "required": ["command"],
    }

    _DANGEROUS_PATTERNS = [
        re.compile(pattern, re.IGNORECASE)
        for pattern in [
            r"(^|\s)(rm|del|erase|rmdir|rd)\b",
            r"\bremove-item\b",
            r"\bgit\s+reset\s+--hard\b",
            r"\bgit\s+clean\b",
            r"\bformat\b",
            r"\bmkfs\b",
            r"\bshutdown\b",
            r"\breboot\b",
        ]
    ]

    def approval_request(self, tool_input: dict, ctx=None) -> ApprovalRequest:
        command = tool_input["command"]
        risk_level = self._classify_command_risk(command)
        command_preview = command.strip().replace("\n", " ")
        if len(command_preview) > 160:
            command_preview = command_preview[:157] + "..."
        return ApprovalRequest(
            tool_name=self.name,
            reason=self.description,
            risk_level=risk_level,
            approval_key=risk_level,
            details=f'command="{command_preview}"',
        )

    def execute(self, tool_input: dict, ctx):
        command = tool_input["command"]
        timeout = int(tool_input.get("timeout_sec", 30))
        if not ctx.session.is_bash_command_allowed(command):
            raise PermissionDeniedError(
                "Bash command is not allowed in this command context. "
                f"Allowed prefixes: {', '.join(ctx.session._active_bash_command_prefixes or ())}"
            )

        if os.name == "nt":
            argv = ["powershell", "-NoProfile", "-Command", command]
        else:
            argv = ["bash", "-lc", command]

        completed = subprocess.run(
            argv,
            cwd=ctx.cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        parts = [f"exit_code: {completed.returncode}"]
        if stdout:
            parts.append(f"stdout:\n{stdout[:12000]}")
        if stderr:
            parts.append(f"stderr:\n{stderr[:12000]}")
        return "\n\n".join(parts)

    def _classify_command_risk(self, command: str) -> str:
        normalized = command.strip()
        for pattern in self._DANGEROUS_PATTERNS:
            if pattern.search(normalized):
                return "dangerous_shell"
        return "shell"
