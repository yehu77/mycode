from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(slots=True)
class ApprovalRequest:
    tool_name: str
    reason: str
    risk_level: str
    approval_key: str | None = None
    details: str = ""


@dataclass(slots=True)
class ApprovalResult:
    decision: str
    scope: str = "once"


class PermissionDeniedError(RuntimeError):
    pass


ApprovalHandler = Callable[[ApprovalRequest], ApprovalResult]


class PermissionManager:
    def __init__(
        self,
        mode: str = "default",
        interactive: bool = True,
        approval_handler: ApprovalHandler | None = None,
    ) -> None:
        self.mode = mode
        self.interactive = interactive
        self.approval_handler = approval_handler
        self._session_allowed_keys: set[str] = set()

    def require_approval(self, request: ApprovalRequest) -> None:
        if self.mode == "bypass":
            return
        if request.risk_level == "read":
            return
        approval_key = request.approval_key or request.risk_level
        if approval_key in self._session_allowed_keys:
            return
        if self.approval_handler is not None:
            result = self.approval_handler(request)
            if result.decision != "allow":
                raise PermissionDeniedError(f'User denied tool "{request.tool_name}".')
            if result.scope == "session":
                self._session_allowed_keys.add(approval_key)
            return
        if not self.interactive:
            raise PermissionDeniedError(
                f'Tool "{request.tool_name}" with risk "{request.risk_level}" '
                "requires approval in non-interactive mode."
            )

        result = self._prompt_for_approval(request)
        if result.decision != "allow":
            raise PermissionDeniedError(f'User denied tool "{request.tool_name}".')
        if result.scope == "session":
            self._session_allowed_keys.add(approval_key)

    def is_session_allowed(self, approval_key: str) -> bool:
        return approval_key in self._session_allowed_keys

    def _prompt_for_approval(self, request: ApprovalRequest) -> ApprovalResult:
        if self.approval_handler is not None:
            return self.approval_handler(request)
        prompt = f"[approve:{request.risk_level}] {request.tool_name}: {request.reason}"
        if request.details:
            prompt += f"\n{request.details}"
        prompt += "\n[o]nce/[s]ession/[n]o: "
        answer = input(prompt).strip().lower()
        if answer in {"o", "once", "y", "yes"}:
            return ApprovalResult(decision="allow", scope="once")
        if answer in {"s", "session", "a", "always"}:
            return ApprovalResult(decision="allow", scope="session")
        return ApprovalResult(decision="deny", scope="once")
