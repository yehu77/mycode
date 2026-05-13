from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess

from ..permissions import ApprovalRequest, PermissionDeniedError
from .base import BaseTool, render_pending_preview, resolve_workspace_path


@dataclass(slots=True)
class ShellCommandSegment:
    index: int
    raw_command: str
    boundary_after: str = ""
    command_name: str = ""
    policy_command: str = ""
    risk_level: str = "shell_read"
    target_paths: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    uncertain: bool = False
    uncertainty_reason: str = ""


@dataclass(slots=True)
class ShellCommandAnalysis:
    command: str
    segments: tuple[ShellCommandSegment, ...]
    risk_level: str
    target_paths: tuple[str, ...]
    complex_features: tuple[str, ...] = ()
    requires_conservative_approval: bool = False


class BashTool(BaseTool):
    name = "bash"
    description = "Run a shell command in the workspace."
    read_only = False
    concurrency_safe = False
    risk_level = "shell_write"
    search_terms = ("shell", "command", "terminal", "powershell", "bash")
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
    _WRITE_PATTERNS = [
        re.compile(pattern, re.IGNORECASE)
        for pattern in [
            r"\b(copy|move|ren|rename|new-item|set-content|add-content|out-file)\b",
            r"(^|\s)(cp|mv|touch|mkdir)\b",
            r"\bgit\s+(add|apply|checkout|commit|mv|restore|rm|stash)\b",
            r"\btee\b",
            r">",
            r">>",
        ]
    ]
    _PATH_FLAGS = {
        "-path",
        "-literalpath",
        "-destination",
        "-outfile",
    }
    _PATH_AWARE_COMMANDS = {
        "add-content",
        "copy",
        "copy-item",
        "cp",
        "del",
        "erase",
        "git add",
        "git apply",
        "git checkout",
        "git mv",
        "git restore",
        "git rm",
        "mkdir",
        "move",
        "move-item",
        "mv",
        "new-item",
        "out-file",
        "rd",
        "ren",
        "rename",
        "rename-item",
        "remove-item",
        "rmdir",
        "set-content",
        "tee",
        "tee-object",
        "touch",
    }
    _SEGMENT_SEPARATORS = {"|", "||", "&&", ";"}
    _SPECIAL_TOKENS = {"&&", "||", "|", ";", "(", ")", "$(", "<("}
    _SAFE_COMMAND_MODE_FEATURES = {"env_assignment"}
    _CONSERVATIVE_COMPLEX_FEATURES = {
        "command_substitution",
        "process_substitution",
        "subshell_group",
        "glob_pattern",
        "unresolved_redirection",
    }

    def approval_request(self, tool_input: dict, ctx=None) -> ApprovalRequest:
        command = tool_input["command"]
        analysis = self.analyze_command(ctx.cwd if ctx is not None else None, command)
        command_preview = command.strip().replace("\n", " ")
        if len(command_preview) > 160:
            command_preview = command_preview[:157] + "..."
        details = self._render_analysis_details(analysis, command_preview=command_preview)
        policy = None
        policy_result = None
        if ctx is not None:
            policy = getattr(ctx.session, "active_command_policy", lambda: None)()
            policy_result = getattr(
                ctx.session,
                "evaluate_bash_command_policy",
                lambda *_args, **_kwargs: None,
            )(command, analysis=analysis)
            details = self._append_command_policy_details(details, policy, policy_result)
        request = ApprovalRequest(
            tool_name=self.name,
            reason=self.description,
            risk_level=analysis.risk_level,
            approval_key=analysis.risk_level,
            details=details,
            command=command,
            command_segments=tuple(segment.raw_command for segment in analysis.segments),
            target_paths=analysis.target_paths,
            command_mode_name=policy.name if ctx is not None and policy is not None else "",
            command_mode_source=policy.source if ctx is not None and policy is not None else "",
            command_mode_allowed_prefixes=(
                tuple(policy.allowed_bash_command_prefixes)
                if ctx is not None and policy is not None and policy.allowed_bash_command_prefixes
                else ()
            ),
            command_mode_violating_segment=(
                policy_result.violating_segment
                if policy_result is not None and not policy_result.allowed
                else ""
            ),
            command_mode_violating_segment_index=(
                policy_result.violating_segment_index
                if policy_result is not None and not policy_result.allowed
                else None
            ),
            command_mode_complex_features=(
                policy_result.violating_features
                if policy_result is not None and not policy_result.allowed
                else ()
            ),
        )
        return request

    def execute(self, tool_input: dict, ctx):
        command = tool_input["command"]
        timeout = int(tool_input.get("timeout_sec", 30))
        analysis = self.analyze_command(ctx.cwd, command)
        self._validate_analysis(analysis, ctx)
        policy_result = ctx.session.evaluate_bash_command_policy(command, analysis=analysis)
        if not policy_result.allowed:
            raise PermissionDeniedError(policy_result.reason)

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
        return self.analyze_command(None, command).risk_level

    def analyze_command(self, cwd: Path | None, command: str) -> ShellCommandAnalysis:
        raw_segments = self._split_command_segments(command)
        segments: list[ShellCommandSegment] = []
        aggregate_paths: list[str] = []
        aggregate_features: list[str] = []
        saw_dangerous = False
        saw_write = False
        saw_conservative = False
        for index, (segment_text, boundary_after) in enumerate(raw_segments, start=1):
            segment = self._analyze_segment(
                cwd,
                segment_text,
                index=index,
                boundary_after=boundary_after,
            )
            if not segment.raw_command:
                continue
            segments.append(segment)
            aggregate_paths.extend(segment.target_paths)
            aggregate_features.extend(segment.features)
            saw_dangerous = saw_dangerous or segment.risk_level == "shell_dangerous"
            saw_write = saw_write or segment.risk_level == "shell_write"
            saw_conservative = saw_conservative or (
                segment.uncertain and segment.risk_level != "shell_read"
            )
        aggregate_risk = "shell_read"
        if saw_dangerous:
            aggregate_risk = "shell_dangerous"
        elif saw_write:
            aggregate_risk = "shell_write"
        return ShellCommandAnalysis(
            command=command,
            segments=tuple(segments),
            risk_level=aggregate_risk,
            target_paths=tuple(dict.fromkeys(aggregate_paths)),
            complex_features=tuple(dict.fromkeys(aggregate_features)),
            requires_conservative_approval=saw_conservative,
        )

    def _classify_segment_risk(self, command: str) -> str:
        normalized = command.strip()
        for pattern in self._DANGEROUS_PATTERNS:
            if pattern.search(normalized):
                return "shell_dangerous"
        for pattern in self._WRITE_PATTERNS:
            if pattern.search(normalized):
                return "shell_write"
        return "shell_read"

    def _analyze_segment(
        self,
        cwd: Path | None,
        segment_text: str,
        *,
        index: int,
        boundary_after: str,
    ) -> ShellCommandSegment:
        tokens = self._split_tokens(segment_text)
        risk_level = self._classify_segment_risk(segment_text)
        features = list(self._segment_features(segment_text, tokens))
        command_name = self._segment_command_name(tokens)
        policy_command = self._segment_policy_command(tokens, segment_text)
        target_paths: tuple[str, ...] = ()
        extraction_issue = ""
        if cwd is not None:
            target_paths, extraction_issue = self._extract_segment_paths(
                cwd,
                tokens,
                risk_level=risk_level,
            )
        if extraction_issue and "redirection" in extraction_issue.casefold():
            features.append("unresolved_redirection")
        features = list(dict.fromkeys(features))
        uncertainty_reason = extraction_issue or self._segment_uncertainty_reason(risk_level, tuple(features))
        uncertain = bool(extraction_issue) or self._features_require_conservative_approval(
            risk_level,
            tuple(features),
        )
        return ShellCommandSegment(
            index=index,
            raw_command=segment_text.strip(),
            boundary_after=boundary_after,
            command_name=command_name,
            policy_command=policy_command,
            risk_level=risk_level,
            target_paths=target_paths,
            features=tuple(features),
            uncertain=uncertain,
            uncertainty_reason=uncertainty_reason,
        )

    def _validate_command(self, command: str, ctx) -> None:
        self._validate_analysis(self.analyze_command(ctx.cwd, command), ctx)

    def _validate_analysis(self, analysis: ShellCommandAnalysis, ctx) -> None:
        if analysis.risk_level == "shell_read":
            return
        for segment in analysis.segments:
            for target in segment.target_paths:
                if not target:
                    continue
                try:
                    resolve_workspace_path(ctx.cwd, target)
                except ValueError as exc:
                    raise PermissionDeniedError(
                        "Shell command references path outside workspace: "
                        f"{target} (segment {segment.index}: {segment.raw_command})"
                    ) from exc

    def _extract_segment_paths(
        self,
        cwd: Path,
        tokens: list[str],
        *,
        risk_level: str,
    ) -> tuple[tuple[str, ...], str]:
        candidates: list[str] = []
        current_command: str | None = None
        pending_path_flag = False
        pending_redirection = False
        positional_paths_remaining = 0
        uncertainty_reason = ""
        awaiting_git_subcommand = False

        for token in tokens:
            if token in {"(", ")", "$(", "<("}:
                continue
            stripped = token.strip("\"'")
            if not stripped:
                continue
            lowered = stripped.casefold()

            if pending_redirection:
                if self._token_can_be_path_value(token):
                    self._maybe_append_path_candidate(candidates, stripped, cwd)
                    pending_redirection = False
                    continue
                if not uncertainty_reason:
                    uncertainty_reason = "redirection target could not be resolved"
                pending_redirection = False

            if pending_path_flag:
                if self._token_can_be_path_value(token):
                    self._maybe_append_path_candidate(candidates, stripped, cwd)
                    pending_path_flag = False
                    continue
                if not uncertainty_reason:
                    uncertainty_reason = "path flag is missing a value"
                pending_path_flag = False

            if self._is_env_assignment_token(stripped) and current_command is None:
                continue

            if self._is_redirection_token(lowered):
                pending_redirection = True
                continue

            if lowered in self._PATH_FLAGS:
                pending_path_flag = True
                continue

            if stripped.startswith("-"):
                continue

            if current_command is None:
                current_command = lowered
                if current_command == "git":
                    awaiting_git_subcommand = True
                    positional_paths_remaining = 0
                else:
                    positional_paths_remaining = self._positional_path_arity(current_command)
                continue

            if awaiting_git_subcommand:
                current_command = f"git {lowered}"
                positional_paths_remaining = self._positional_path_arity(current_command)
                awaiting_git_subcommand = False
                continue

            if positional_paths_remaining > 0:
                self._maybe_append_path_candidate(candidates, stripped, cwd)
                positional_paths_remaining -= 1
                continue

            if current_command in self._PATH_AWARE_COMMANDS:
                self._maybe_append_path_candidate(candidates, stripped, cwd)

        if pending_redirection and not uncertainty_reason:
            uncertainty_reason = "redirection target could not be resolved"
        if pending_path_flag and not uncertainty_reason and risk_level != "shell_read":
            uncertainty_reason = "path flag is missing a value"
        return tuple(dict.fromkeys(candidates)), uncertainty_reason

    def _split_tokens(self, command: str) -> list[str]:
        tokens: list[str] = []
        current: list[str] = []
        index = 0
        in_single = False
        in_double = False

        def flush() -> None:
            if current:
                tokens.append("".join(current))
                current.clear()

        while index < len(command):
            char = command[index]

            if in_single:
                current.append(char)
                if char == "'":
                    in_single = False
                index += 1
                continue

            if in_double:
                current.append(char)
                if char == '"':
                    in_double = False
                index += 1
                continue

            if char.isspace():
                flush()
                index += 1
                continue

            if char == "'":
                current.append(char)
                in_single = True
                index += 1
                continue

            if char == '"':
                current.append(char)
                in_double = True
                index += 1
                continue

            if char == "$" and index + 1 < len(command) and command[index + 1] == "(":
                flush()
                tokens.append("$(")
                index += 2
                continue

            if char == "<" and index + 1 < len(command) and command[index + 1] == "(":
                flush()
                tokens.append("<(")
                index += 2
                continue

            if char in {"&", "|"}:
                flush()
                if index + 1 < len(command) and command[index + 1] == char:
                    tokens.append(char * 2)
                    index += 2
                else:
                    tokens.append(char)
                    index += 1
                continue

            if char == ";":
                flush()
                tokens.append(char)
                index += 1
                continue

            if char in {"(", ")"}:
                flush()
                tokens.append(char)
                index += 1
                continue

            if char.isdigit():
                start = index
                while index < len(command) and command[index].isdigit():
                    index += 1
                if index < len(command) and command[index] in {">", "<"} and not current:
                    op = command[index]
                    token = command[start:index] + op
                    index += 1
                    if index < len(command) and command[index] == op:
                        token += op
                        index += 1
                    tokens.append(token)
                    continue
                current.extend(command[start:index])
                continue

            if char in {">", "<"}:
                flush()
                token = char
                index += 1
                if index < len(command) and command[index] == char:
                    token += char
                    index += 1
                tokens.append(token)
                continue

            current.append(char)
            index += 1

        flush()
        return tokens

    def _split_command_segments(self, command: str) -> list[tuple[str, str]]:
        if not command.strip():
            return [(command.strip(), "")]
        segments: list[tuple[str, str]] = []
        start = 0
        index = 0
        depth = 0
        in_single = False
        in_double = False
        while index < len(command):
            char = command[index]
            if in_single:
                if char == "'":
                    in_single = False
                index += 1
                continue
            if in_double:
                if char == '"':
                    in_double = False
                index += 1
                continue
            if char == "'":
                in_single = True
                index += 1
                continue
            if char == '"':
                in_double = True
                index += 1
                continue
            if char == "$" and index + 1 < len(command) and command[index + 1] == "(":
                depth += 1
                index += 2
                continue
            if char == "<" and index + 1 < len(command) and command[index + 1] == "(":
                depth += 1
                index += 2
                continue
            if char == "(":
                depth += 1
                index += 1
                continue
            if char == ")":
                if depth > 0:
                    depth -= 1
                index += 1
                continue
            if depth == 0:
                boundary = ""
                if command.startswith("&&", index) or command.startswith("||", index):
                    boundary = command[index : index + 2]
                elif char in {"|", ";"}:
                    boundary = char
                if boundary:
                    segment_text = command[start:index].strip()
                    if segment_text:
                        segments.append((segment_text, boundary))
                    index += len(boundary)
                    start = index
                    continue
            index += 1
        trailing = command[start:].strip()
        if trailing:
            segments.append((trailing, ""))
        return segments or [(command.strip(), "")]

    def _is_redirection_token(self, token: str) -> bool:
        return bool(re.fullmatch(r"(?:\*|\d*)?(?:>>?|<<?)", token))

    def _token_can_be_path_value(self, token: str) -> bool:
        return token not in self._SPECIAL_TOKENS and not self._is_redirection_token(token.casefold())

    def _positional_path_arity(self, command_name: str) -> int:
        if command_name in {
            "cp",
            "copy",
            "copy-item",
            "git mv",
            "move",
            "move-item",
            "mv",
            "ren",
            "rename",
            "rename-item",
        }:
            return 2
        if command_name in self._PATH_AWARE_COMMANDS:
            return 1
        return 0

    def _maybe_append_path_candidate(self, candidates: list[str], token: str, cwd: Path) -> None:
        stripped = token.strip("\"'")
        if not stripped:
            return
        if stripped.startswith("-"):
            return
        if stripped.startswith("http://") or stripped.startswith("https://"):
            return
        if not self._looks_like_path(stripped, cwd):
            return
        candidates.append(stripped)

    def _looks_like_path(self, token: str, cwd: Path) -> bool:
        if re.match(r"^[A-Za-z]:[/\\]", token) or token.startswith(("/", ".\\", "./", "..\\", "../")):
            return True
        if "/" in token or "\\" in token:
            return True
        if token.startswith("."):
            return True
        candidate_path = cwd / token
        return candidate_path.parent != cwd or bool(candidate_path.suffix)

    def _segment_command_name(self, tokens: list[str]) -> str:
        for token in tokens:
            stripped = token.strip("\"'")
            if not stripped or stripped.startswith("-"):
                continue
            if token in self._SPECIAL_TOKENS or self._is_redirection_token(stripped.casefold()):
                continue
            if self._is_env_assignment_token(stripped):
                continue
            return stripped.casefold()
        return ""

    def _segment_policy_command(self, tokens: list[str], segment_text: str) -> str:
        filtered: list[str] = []
        skipping_assignments = True
        for token in tokens:
            stripped = token.strip("\"'")
            if not stripped:
                continue
            if skipping_assignments and self._is_env_assignment_token(stripped):
                continue
            skipping_assignments = False
            filtered.append(token)
        return " ".join(filtered).strip() or segment_text.strip()

    def _segment_features(self, segment_text: str, tokens: list[str]) -> tuple[str, ...]:
        features: list[str] = []
        if "$(" in segment_text or "$(" in tokens:
            features.append("command_substitution")
        if "<(" in segment_text or "<(" in tokens:
            features.append("process_substitution")
        if "(" in tokens:
            features.append("subshell_group")
        if self._leading_env_assignment_count(tokens) > 0:
            features.append("env_assignment")
        if any(self._token_has_glob_pattern(token) for token in tokens):
            features.append("glob_pattern")
        return tuple(dict.fromkeys(features))

    def _segment_uncertainty_reason(
        self,
        risk_level: str,
        features: tuple[str, ...],
    ) -> str:
        if risk_level == "shell_read":
            return ""
        for feature in features:
            if feature in self._CONSERVATIVE_COMPLEX_FEATURES:
                return f"complex_feature={feature}"
        return ""

    def _features_require_conservative_approval(
        self,
        risk_level: str,
        features: tuple[str, ...],
    ) -> bool:
        if risk_level == "shell_read":
            return False
        return any(feature in self._CONSERVATIVE_COMPLEX_FEATURES for feature in features)

    def _leading_env_assignment_count(self, tokens: list[str]) -> int:
        count = 0
        for token in tokens:
            stripped = token.strip("\"'")
            if not stripped:
                continue
            if self._is_env_assignment_token(stripped):
                count += 1
                continue
            break
        return count

    def _is_env_assignment_token(self, token: str) -> bool:
        return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", token))

    def _token_has_glob_pattern(self, token: str) -> bool:
        if token in self._SPECIAL_TOKENS:
            return False
        if self._is_redirection_token(token.casefold()):
            return False
        if (token.startswith("'") and token.endswith("'")) or (
            token.startswith('"') and token.endswith('"')
        ):
            return False
        return "*" in token or "?" in token

    def _render_analysis_details(
        self,
        analysis: ShellCommandAnalysis,
        *,
        command_preview: str,
    ) -> str:
        summary_lines = [
            f'command="{command_preview}"',
            f"segments: {len(analysis.segments)}",
            f"aggregate_risk: {analysis.risk_level}",
        ]
        if analysis.complex_features:
            summary_lines.append("complex_features: " + ", ".join(analysis.complex_features))
        if analysis.requires_conservative_approval:
            summary_lines.append("analysis: conservative approval required")
        sections: list[tuple[str, str]] = []
        for segment in analysis.segments:
            body_lines = [
                f"command: {segment.raw_command}",
                f"risk: {segment.risk_level}",
            ]
            if segment.policy_command and segment.policy_command != segment.raw_command:
                body_lines.append("policy_command: " + segment.policy_command)
            if segment.target_paths:
                body_lines.append("paths: " + ", ".join(segment.target_paths))
            if segment.features:
                body_lines.append("complex_features: " + ", ".join(segment.features))
            if segment.uncertain and segment.uncertainty_reason:
                body_lines.append("analysis: " + segment.uncertainty_reason)
            if segment.boundary_after:
                body_lines.append(f"next: {segment.boundary_after}")
            sections.append((f"segment {segment.index}", "\n".join(body_lines)))
        return render_pending_preview(
            "Pending shell command",
            summary_lines=summary_lines,
            sections=sections,
            max_lines=32,
        )

    def _append_command_policy_details(self, details: str, policy, policy_result) -> str:
        if policy is None or not getattr(policy, "allowed_bash_command_prefixes", None):
            return details
        suffix_lines = [
            "",
            "Command policy:",
            f"- mode: {policy.name}",
            f"- source: {policy.source}",
            "- allowed_prefixes: " + ", ".join(policy.allowed_bash_command_prefixes),
        ]
        if (
            policy_result is not None
            and not getattr(policy_result, "allowed", True)
            and getattr(policy_result, "violating_features", ())
        ):
            suffix_lines.append(
                "- complex_features: "
                + ", ".join(getattr(policy_result, "violating_features", ()))
            )
        if policy_result is not None and not getattr(policy_result, "allowed", True):
            suffix_lines.append("- violation: " + getattr(policy_result, "reason", "rejected"))
        return details.rstrip() + "\n" + "\n".join(suffix_lines)
