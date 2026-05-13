from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable


@dataclass(slots=True)
class ApprovalRequest:
    tool_name: str
    reason: str
    risk_level: str
    approval_key: str | None = None
    details: str = ""
    command: str = ""
    command_segments: tuple[str, ...] = ()
    target_paths: tuple[str, ...] = ()
    permission_rules: tuple[str, ...] = ()
    decision_reason: str = ""
    command_mode_name: str = ""
    command_mode_source: str = ""
    command_mode_allowed_prefixes: tuple[str, ...] = ()
    command_mode_violating_segment: str = ""
    command_mode_violating_segment_index: int | None = None
    command_mode_complex_features: tuple[str, ...] = ()


@dataclass(slots=True)
class ApprovalResult:
    decision: str
    scope: str = "once"


class PermissionDeniedError(RuntimeError):
    pass


ApprovalHandler = Callable[[ApprovalRequest], ApprovalResult]


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PermissionRuleScope(str, Enum):
    TOOL = "tool"
    SHELL = "shell"
    PATH = "path"
    RISK = "risk"


@dataclass(slots=True, frozen=True)
class PermissionRule:
    decision: PermissionDecision
    scope: PermissionRuleScope
    value: str

    def describe(self) -> str:
        return f"{self.decision.value}:{self.scope.value}:{self.value}"


@dataclass(slots=True, frozen=True)
class PermissionDecisionResult:
    decision: PermissionDecision
    matched_rules: tuple[str, ...] = ()


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
        self._workspace_rules: list[PermissionRule] = []
        self._session_rules: list[PermissionRule] = []

    def add_rule(self, rule: PermissionRule) -> None:
        self._session_rules.append(rule)

    def extend_rules(self, rules: list[PermissionRule]) -> None:
        self._session_rules.extend(rules)

    def clear_rules(self) -> None:
        self._workspace_rules.clear()
        self._session_rules.clear()

    def set_workspace_rules(self, rules: list[PermissionRule]) -> None:
        self._workspace_rules = list(rules)

    def set_session_rules(self, rules: list[PermissionRule]) -> None:
        self._session_rules = list(rules)

    @property
    def workspace_rules(self) -> tuple[PermissionRule, ...]:
        return tuple(self._workspace_rules)

    @property
    def session_rules(self) -> tuple[PermissionRule, ...]:
        return tuple(self._session_rules)

    @property
    def rules(self) -> tuple[PermissionRule, ...]:
        return tuple([*self._workspace_rules, *self._session_rules])

    def require_approval(self, request: ApprovalRequest) -> None:
        if self.mode == "bypass":
            return
        decision = self.evaluate(request)
        if decision.decision == PermissionDecision.ALLOW:
            return
        if decision.decision == PermissionDecision.DENY:
            raise PermissionDeniedError(self._deny_message(_with_decision_context(request, decision)))

        approval_key = request.approval_key or request.risk_level
        if approval_key in self._session_allowed_keys:
            return
        enriched_request = _with_decision_context(request, decision)
        if self.approval_handler is not None:
            result = self.approval_handler(enriched_request)
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

        result = self._prompt_for_approval(enriched_request)
        if result.decision != "allow":
            raise PermissionDeniedError(f'User denied tool "{request.tool_name}".')
        if result.scope == "session":
            self._session_allowed_keys.add(approval_key)

    def evaluate(self, request: ApprovalRequest) -> PermissionDecisionResult:
        deny_matches = self._matching_rules(request, PermissionDecision.DENY)
        if deny_matches:
            return PermissionDecisionResult(PermissionDecision.DENY, deny_matches)

        approval_key = request.approval_key or request.risk_level
        if approval_key in self._session_allowed_keys:
            return PermissionDecisionResult(PermissionDecision.ALLOW, (f"session:{approval_key}",))

        ask_matches = self._matching_rules(request, PermissionDecision.ASK)
        allow_matches = self._matching_rules(request, PermissionDecision.ALLOW)
        if ask_matches:
            return PermissionDecisionResult(PermissionDecision.ASK, ask_matches)
        if allow_matches:
            return PermissionDecisionResult(PermissionDecision.ALLOW, allow_matches)
        return PermissionDecisionResult(self._baseline_decision(request))

    def is_session_allowed(self, approval_key: str) -> bool:
        return approval_key in self._session_allowed_keys

    def _matching_rules(
        self,
        request: ApprovalRequest,
        decision: PermissionDecision,
    ) -> tuple[str, ...]:
        matches: list[str] = []
        for rule in self.rules:
            if rule.decision != decision:
                continue
            matches.extend(self._rule_match_descriptions(rule, request))
        return tuple(matches)

    def _rule_match_descriptions(
        self,
        rule: PermissionRule,
        request: ApprovalRequest,
    ) -> tuple[str, ...]:
        value = rule.value.casefold()
        if rule.scope == PermissionRuleScope.TOOL:
            return (rule.describe(),) if request.tool_name.casefold() == value else ()
        if rule.scope == PermissionRuleScope.RISK:
            risk = (request.approval_key or request.risk_level).casefold()
            if request.risk_level.casefold() == value or risk == value:
                return (rule.describe(),)
            return ()
        if rule.scope == PermissionRuleScope.SHELL:
            segments = request.command_segments or ((request.command.strip(),) if request.command.strip() else ())
            matches: list[str] = []
            for index, segment in enumerate(segments, start=1):
                normalized = segment.strip().casefold()
                if not normalized or not normalized.startswith(value):
                    continue
                preview = segment.strip().replace("\n", " ")
                if len(preview) > 80:
                    preview = preview[:77] + "..."
                matches.append(f"{rule.describe()} [segment {index}: {preview}]")
            return tuple(matches)
        if rule.scope == PermissionRuleScope.PATH:
            for target in request.target_paths:
                normalized = _normalize_rule_path(target)
                if normalized == value or normalized.startswith(value.rstrip("/") + "/"):
                    return (f"{rule.describe()} [path: {target}]",)
            return ()
        return ()

    def _baseline_decision(self, request: ApprovalRequest) -> PermissionDecision:
        if request.risk_level in {"read", "shell_read"}:
            return PermissionDecision.ALLOW
        return PermissionDecision.ASK

    def _deny_message(
        self,
        request: ApprovalRequest,
    ) -> str:
        if request.permission_rules:
            rules = ", ".join(request.permission_rules)
            detail = f" Reason: {request.decision_reason}" if request.decision_reason else ""
            return f'Tool "{request.tool_name}" is denied by session permission rules: {rules}.{detail}'
        return f'Tool "{request.tool_name}" is denied by permission policy.'

    def _prompt_for_approval(self, request: ApprovalRequest) -> ApprovalResult:
        if self.approval_handler is not None:
            return self.approval_handler(request)
        prompt = f"[approve:{request.risk_level}] {request.tool_name}: {request.reason}"
        if request.decision_reason:
            prompt += "\npolicy: " + request.decision_reason
        if request.command_mode_name:
            prompt += "\ncommand_mode:"
            prompt += f"\n- mode: {request.command_mode_name}"
            if request.command_mode_source:
                prompt += f"\n- source: {request.command_mode_source}"
            if request.command_mode_allowed_prefixes:
                prompt += "\n- allowed_prefixes: " + ", ".join(request.command_mode_allowed_prefixes)
            if request.command_mode_violating_segment:
                segment_label = request.command_mode_violating_segment
                if request.command_mode_violating_segment_index is not None:
                    segment_label = (
                        f"segment {request.command_mode_violating_segment_index}: "
                        f"{request.command_mode_violating_segment}"
                    )
                prompt += f"\n- violating_segment: {segment_label}"
            if request.command_mode_complex_features:
                prompt += "\n- complex_features: " + ", ".join(request.command_mode_complex_features)
        if request.permission_rules:
            prompt += "\nmatched rules: " + ", ".join(request.permission_rules)
        if request.target_paths:
            prompt += "\npaths: " + ", ".join(request.target_paths)
        if request.command:
            prompt += "\ncommand: " + request.command.strip()
        if request.details:
            prompt += f"\n{request.details}"
        prompt += "\n[o]nce/[s]ession/[n]o: "
        answer = input(prompt).strip().lower()
        if answer in {"o", "once", "y", "yes"}:
            return ApprovalResult(decision="allow", scope="once")
        if answer in {"s", "session", "a", "always"}:
            return ApprovalResult(decision="allow", scope="session")
        return ApprovalResult(decision="deny", scope="once")


def workspace_relative_paths(cwd: Path, paths: list[Path]) -> tuple[str, ...]:
    relative: list[str] = []
    for path in paths:
        resolved = path.resolve()
        try:
            rel = resolved.relative_to(cwd.resolve()).as_posix()
        except ValueError:
            rel = resolved.as_posix()
        relative.append(rel)
    return tuple(relative)


def _normalize_rule_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip().strip("/")
    return normalized.casefold()


def _decision_reason(request: ApprovalRequest, decision: PermissionDecisionResult) -> str:
    if decision.matched_rules:
        if decision.decision == PermissionDecision.ASK:
            return "Matched ask rules: " + ", ".join(decision.matched_rules)
        if decision.decision == PermissionDecision.DENY:
            return "Matched deny rules: " + ", ".join(decision.matched_rules)
        return "Matched allow rules: " + ", ".join(decision.matched_rules)
    if decision.decision == PermissionDecision.ASK:
        return f'Baseline policy requires approval for risk "{request.risk_level}".'
    if decision.decision == PermissionDecision.DENY:
        return "Permission policy denied this action."
    return ""


def _with_rule_matches(request: ApprovalRequest, matches: tuple[str, ...]) -> ApprovalRequest:
    request.permission_rules = matches
    return request


def _with_decision_context(
    request: ApprovalRequest,
    decision: PermissionDecisionResult,
) -> ApprovalRequest:
    request.permission_rules = decision.matched_rules
    request.decision_reason = _decision_reason(request, decision)
    return request
