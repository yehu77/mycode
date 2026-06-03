from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import Thread
from time import sleep
from typing import TYPE_CHECKING

from .background_metadata import background_grouped_actions, background_session_metadata, background_workflow_payload
from .commands import CommandExecution, build_default_command_registry, render_repl_command_help
from .config import load_config
from .env_loader import load_dotenv
from .remote_session import RemoteSessionProxy
from .runtime.headless import (
    collect_references_headless,
    diff_targets_headless,
    locate_symbol_headless,
    open_file_target_headless,
    reference_targets_headless,
    open_symbol_target_headless,
    run_headless,
    symbol_actions_headless,
)
from .runtime.events import RuntimeEvent
from .session import Session, workspace_recommended_actions
from .session_factory import SessionFactory
from .service import BridgeTcpServer, JsonRpcStdioService, ServiceDispatcher
from .storage.background_sessions import (
    BackgroundSessionRecord,
    create_background_session,
    get_background_session_log_path,
    list_background_sessions,
    resolve_background_session,
    update_background_session,
)
from .storage.transcript import get_session_path, list_transcripts
from .session_components.workflow_surface import render_summary_field_lines, render_workflow_action_sections
from .workspace import IsolatedWorkspace, cleanup_isolated_workspace, prepare_isolated_workspace
from .workspace.isolation import derive_workspace_health

if TYPE_CHECKING:
    from .tui import run_tui_app as _run_tui_app

REPL_COMMAND_HELP = render_repl_command_help(build_default_command_registry())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pyclaude", description="Python ClaudeCode MVP")
    parser.add_argument("--cwd", default=str(Path.cwd()), help="Workspace directory")
    parser.add_argument(
        "--provider",
        choices=["anthropic", "openai-compatible"],
        default=None,
        help="Model provider backend",
    )
    parser.add_argument("--api-key", default=None, help="API key override")
    parser.add_argument("--base-url", default=None, help="Base URL for OpenAI-compatible APIs")
    parser.add_argument("--mcp-config", default=None, help="Path to MCP server config JSON")
    parser.add_argument("--permission-config", default=None, help="Path to permission rules JSON")
    parser.add_argument("--model", default=None, help="Model name")
    parser.add_argument("--resume-session", default=None, help="Resume a specific saved session by id")
    parser.add_argument("--max-turns", type=int, default=None, help="Maximum tool-use turns")
    parser.add_argument("--max-tokens", type=int, default=None, help="Output token budget")
    parser.add_argument(
        "--permission-mode",
        choices=["default", "bypass"],
        default=None,
        help="Tool approval policy",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    ask = subparsers.add_parser("ask", help="Run a single prompt")
    ask.add_argument("prompt", help="Prompt to send")
    ask.add_argument("--background", "--bg", action="store_true", help="Run the prompt in a detached background session")

    subparsers.add_parser("repl", help="Start interactive REPL")
    subparsers.add_parser("tui", help="Start terminal UI")
    subparsers.add_parser("serve-stdio", help="Start a local JSON-RPC stdio service")
    serve_bridge = subparsers.add_parser("serve-bridge", help="Start a local TCP bridge service")
    serve_bridge.add_argument("--host", default="127.0.0.1", help="Bridge bind host")
    serve_bridge.add_argument("--port", type=int, default=8765, help="Bridge bind port")
    sessions = subparsers.add_parser("sessions", help="List saved sessions")
    sessions.add_argument("--limit", type=int, default=10, help="Maximum sessions to show")
    subparsers.add_parser("agents", help="Inspect lightweight local agent definitions")
    bg_ps = subparsers.add_parser("ps", help="List detached background sessions")
    bg_ps.add_argument("--limit", type=int, default=20, help="Maximum background sessions to show")
    bg_ps.add_argument("session", nargs="?", help="Optional background session id or prefix for detail view")
    bg_logs = subparsers.add_parser("logs", help="Print background session logs")
    bg_logs.add_argument("session", help="Background session id or prefix")
    bg_logs.add_argument("view", nargs="?", choices=["summary", "tail"], default="tail", help="Log view mode")
    bg_attach = subparsers.add_parser("attach", help="Reattach to a live background session")
    bg_attach.add_argument("session", help="Background session id or prefix")
    bg_attach.add_argument(
        "--mode",
        choices=["repl", "tui"],
        default="repl",
        help="Reattach using the REPL or TUI frontend",
    )
    bg_kill = subparsers.add_parser("kill", help="Stop a background session")
    bg_kill.add_argument("session", help="Background session id or prefix")
    bg_worker = subparsers.add_parser("_bg-runner", help=argparse.SUPPRESS)
    bg_worker.add_argument("--bg-id", required=True, help=argparse.SUPPRESS)
    bg_worker.add_argument("prompt", help=argparse.SUPPRESS)
    locate = subparsers.add_parser("locate-symbol", help="Locate symbol definitions in a structured form")
    locate.add_argument("symbol", help="Symbol name to locate")
    locate.add_argument("--path", default=".", help="Optional file or subdirectory scope")
    locate.add_argument("--max-results", type=int, default=50, help="Maximum matches to return")
    locate.add_argument("--json", action="store_true", help="Print JSON output")
    refs = subparsers.add_parser("references", help="Collect symbol references in a structured form")
    refs.add_argument("symbol", help="Symbol name to analyze")
    refs.add_argument("--path", default=".", help="Optional file or subdirectory scope")
    refs.add_argument(
        "--scope",
        choices=["auto", "current_file", "current_dir", "workspace"],
        default="auto",
        help="Reference search scope",
    )
    refs.add_argument("--max-results", type=int, default=100, help="Maximum references to return")
    refs.add_argument("--json", action="store_true", help="Print JSON output")
    open_file = subparsers.add_parser("open-file", help="Build an IDE-friendly open-file target")
    open_file.add_argument("path", help="Workspace-relative file path")
    open_file.add_argument("--line", type=int, default=1, help="1-based line number")
    open_file.add_argument("--column", type=int, default=1, help="1-based column number")
    open_file.add_argument("--end-line", type=int, default=None, help="Optional end line")
    open_file.add_argument("--end-column", type=int, default=None, help="Optional end column")
    open_file.add_argument("--label", default="", help="Optional target label")
    open_file.add_argument("--json", action="store_true", help="Print JSON output")
    open_symbol = subparsers.add_parser("open-symbol", help="Build an IDE-friendly target for a symbol definition")
    open_symbol.add_argument("symbol", help="Symbol name to open")
    open_symbol.add_argument("--path", default=".", help="Optional file or subdirectory scope")
    open_symbol.add_argument("--match-index", type=int, default=0, help="Which symbol match to open")
    open_symbol.add_argument("--json", action="store_true", help="Print JSON output")
    diff_targets = subparsers.add_parser("diff-targets", help="Build IDE-friendly diff navigation targets")
    diff_targets.add_argument("path", help="Workspace-relative file path for the diff")
    diff_targets.add_argument("--before-file", required=True, help="Path to previous file contents")
    diff_targets.add_argument("--after-file", required=True, help="Path to updated file contents")
    diff_targets.add_argument("--json", action="store_true", help="Print JSON output")
    ref_targets = subparsers.add_parser("reference-targets", help="Build IDE-friendly jump targets for symbol references")
    ref_targets.add_argument("symbol", help="Symbol name to analyze")
    ref_targets.add_argument("--path", default=".", help="Optional file or subdirectory scope")
    ref_targets.add_argument(
        "--scope",
        choices=["auto", "current_file", "current_dir", "workspace"],
        default="auto",
        help="Reference search scope",
    )
    ref_targets.add_argument("--max-results", type=int, default=100, help="Maximum references to return")
    ref_targets.add_argument("--json", action="store_true", help="Print JSON output")
    symbol_actions = subparsers.add_parser("symbol-actions", help="Build IDE-friendly definition and reference actions for a symbol")
    symbol_actions.add_argument("symbol", help="Symbol name to analyze")
    symbol_actions.add_argument("--path", default=".", help="Optional file or subdirectory scope")
    symbol_actions.add_argument(
        "--scope",
        choices=["auto", "current_file", "current_dir", "workspace"],
        default="workspace",
        help="Reference search scope",
    )
    symbol_actions.add_argument("--max-definition-results", type=int, default=50, help="Maximum definitions to return")
    symbol_actions.add_argument("--max-reference-results", type=int, default=100, help="Maximum references to return")
    symbol_actions.add_argument("--json", action="store_true", help="Print JSON output")
    mcp_call = subparsers.add_parser("mcp-call", help="Call an MCP tool directly for diagnosis")
    mcp_call.add_argument("server", help="MCP server name")
    mcp_call.add_argument("tool", help="MCP tool name")
    mcp_call.add_argument("--args", default="{}", help="JSON object arguments for the MCP tool")
    mcp_call.add_argument("--json", action="store_true", help="Print JSON output")
    mcp_verify = subparsers.add_parser("mcp-verify", help="Verify MCP end-to-end through the model")
    mcp_verify.add_argument("server", help="MCP server name")
    mcp_verify.add_argument("tool", help="MCP tool name")
    mcp_verify.add_argument("--args", default="{}", help="JSON object arguments for the MCP tool")
    mcp_verify.add_argument("--json", action="store_true", help="Print JSON output")
    return parser


class _ConsoleEventRenderer:
    def __init__(self, *, suppress_assistant_text: bool = False) -> None:
        self.suppress_assistant_text = suppress_assistant_text
        self._assistant_open = False

    def __call__(self, event: RuntimeEvent) -> None:
        if event.kind == "assistant_text":
            self._render_assistant_text(event.message)
            return
        self._close_assistant_line()
        if event.kind == "assistant_tool_call":
            print(f"[assistant->tools] {event.message}")
            return
        if event.kind == "assistant_tool_result_ready":
            print(f"[tools->assistant] {event.message}")
            return
        if event.kind == "plan_execution":
            print(f"[plan] {event.message}")
            return
        if event.kind in {
            "advisor",
            "advisor_review_started",
            "advisor_review_result",
            "advisor_revision_requested",
            "advisor_error",
        }:
            print(f"[advisor] {event.message}")
            return
        if event.kind == "context_compacted":
            print(f"[context] {event.message}")
            return
        if event.kind == "provider_retry":
            print(f"[provider:retry] {event.message}")
            return
        if event.kind == "tool_started":
            tool_name = event.tool_name or "unknown"
            print(f"[tool:start] {tool_name} {event.message}")
            return
        if event.kind == "tool_finished":
            tool_name = event.tool_name or "unknown"
            suffix = f" in {event.duration_ms}ms" if event.duration_ms is not None else ""
            print(f"[tool:ok] {tool_name}{suffix}")
            return
        if event.kind == "tool_failed":
            tool_name = event.tool_name or "unknown"
            suffix = f" in {event.duration_ms}ms" if event.duration_ms is not None else ""
            print(f"[tool:error] {tool_name}{suffix}: {event.message}")
            return

    def finish(self) -> None:
        self._close_assistant_line()

    def _render_assistant_text(self, text: str) -> None:
        if self.suppress_assistant_text:
            return
        print(text, end="", flush=True)
        self._assistant_open = True

    def _close_assistant_line(self) -> None:
        if not self._assistant_open:
            return
        print()
        self._assistant_open = False


def _handle_repl_command(session, prompt: str) -> tuple[bool, str | CommandExecution | None]:
    if hasattr(session, "handle_repl_command"):
        return session.handle_repl_command(prompt)
    if prompt == "/help":
        return True, REPL_COMMAND_HELP
    if prompt == "/context-refresh":
        return True, session.reload_project_context()
    return session.command_registry.handle(session, prompt)


def _session_source_header_lines(
    session: Session,
    *,
    session_source: str,
    restored_from: Path | None = None,
    live_background_id: str | None = None,
) -> list[str]:
    lines = [f"session_source: {session_source}"]
    if restored_from is not None:
        lines.append(f"restored_from: {restored_from}")
        lines.append("resume_semantics: saved session resume restores state only; live work requires attach.")
    elif live_background_id:
        lines.append(f"background_session: {live_background_id}")
        lines.append("resume_semantics: attached to live background work; /exit detaches without stopping it.")
    else:
        lines.append("resume_semantics: new live session.")
    planning_artifact_id = getattr(session.state, "active_planning_artifact_id", None)
    if planning_artifact_id:
        lines.append(f"active_plan: {planning_artifact_id}")
    if hasattr(session, "task_surface_summary_lines"):
        counts = session.task_surface_counts_payload()
        total = sum(int(value) for value in counts.values())
        lines.append(f"task_surface_total: {total}")
    lines.extend(_focused_file_context_header_lines(session))
    return lines


def _focused_file_context_header_lines(session: Session) -> list[str]:
    if not hasattr(session, "working_set_payload"):
        return []
    try:
        payload = session.working_set_payload()
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(payload, dict):
        return []
    files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
    if not files:
        return []
    primary = files[0]
    path = str(primary.get("path") or payload.get("file_context_primary_path") or "").strip()
    source = str(primary.get("source") or "working_set").strip() or "working_set"
    primary_target = primary.get("target") or payload.get("file_context_primary_target")
    secondary_target = _focused_file_context_secondary_target(primary) or payload.get(
        "file_context_primary_diff_targets"
    )
    lines = [
        "focused_file_context: "
        + "  ".join(
            part
            for part in (
                f"source={source}",
                f"path={path}" if path else "",
                (
                    "primary=" + _format_target_summary(primary_target)
                    if _format_target_summary(primary_target)
                    else ""
                ),
                (
                    "secondary=" + _format_target_summary(secondary_target)
                    if _format_target_summary(secondary_target)
                    else ""
                ),
            )
            if part
        )
    ]
    lines.append(f"focused_file_context_count: {len(files)}")
    return lines


def _focused_file_context_secondary_target(item: dict[str, object]) -> dict[str, object] | None:
    diff_targets = item.get("diff_targets")
    if isinstance(diff_targets, dict):
        hunks = diff_targets.get("hunks")
        if isinstance(hunks, list):
            for hunk in hunks:
                if isinstance(hunk, dict):
                    return dict(hunk)
        return dict(diff_targets)
    if isinstance(diff_targets, list):
        for hunk in diff_targets:
            if isinstance(hunk, dict):
                return dict(hunk)
    return None


def _format_target_summary(target: object) -> str | None:
    if not isinstance(target, dict):
        return None
    action = str(target.get("action") or "").strip()
    path = str(target.get("path") or "").strip()
    line = target.get("line")
    label = str(target.get("label") or "").strip()
    parts: list[str] = []
    if action:
        parts.append(action)
    if path:
        location = path
        if line not in (None, ""):
            location += f":{line}"
        parts.append(location)
    if label:
        parts.append(label)
    return " ".join(parts) if parts else None


def run_repl(
    session: Session,
    *,
    session_source: str = "new",
    restored_from: Path | None = None,
    live_background_id: str | None = None,
) -> int:
    print(f"PyClaudeCode REPL in {session.config.cwd}")
    if session.state.messages and restored_from is not None:
        print(
            f'Restored saved session {session.state.session_id} with {len(session.state.messages)} messages.'
        )
    elif session.state.messages and live_background_id:
        print(
            f'Attached to live session {session.state.session_id} with {len(session.state.messages)} messages.'
        )
    for line in _session_source_header_lines(
        session,
        session_source=session_source,
        restored_from=restored_from,
        live_background_id=live_background_id,
    ):
        print(line)
    workspace_summary = _render_workspace_summary_line_from_state(session.state)
    if workspace_summary:
        print(workspace_summary)
    print(_render_execution_summary_line_from_state(session.state))
    for line in _render_workspace_guidance_lines_from_state(session.state):
        print(line)
    if hasattr(session, "task_surface_summary_lines"):
        for line in session.task_surface_summary_lines():
            print(line)
    print('Type "/help" for commands.')
    while True:
        try:
            prompt = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not prompt:
            continue
        if prompt == "/exit":
            return 0
        handled, output = _handle_repl_command(session, prompt)
        if handled:
            if isinstance(output, CommandExecution):
                renderer = _ConsoleEventRenderer()
                try:
                    session.run_command(output, sink=renderer)
                except Exception as exc:  # noqa: BLE001
                    renderer.finish()
                    print(f"error: {type(exc).__name__}: {exc}")
                else:
                    renderer.finish()
                continue
            if output:
                print(output)
            continue
        renderer = _ConsoleEventRenderer()
        try:
            session.ask(prompt, sink=renderer)
        except Exception as exc:  # noqa: BLE001
            renderer.finish()
            print(f"error: {type(exc).__name__}: {exc}")
        else:
            renderer.finish()


def _launch_tui(
    session: Session,
    *,
    session_source: str = "new",
    restored_from: Path | None = None,
    live_background_id: str | None = None,
) -> int:
    try:
        from .tui import run_tui_app
    except ImportError as exc:
        if exc.name == "textual" or "textual" in str(exc):
            raise RuntimeError('Missing dependency "textual". Install with: pip install -e .[tui]') from exc
        raise
    return run_tui_app(
        session,
        session_source=session_source,
        restored_from=restored_from,
        live_background_id=live_background_id,
    )


def run_tui(
    session: Session,
    *,
    session_source: str = "new",
    restored_from: Path | None = None,
    live_background_id: str | None = None,
) -> int:
    return _launch_tui(
        session,
        session_source=session_source,
        restored_from=restored_from,
        live_background_id=live_background_id,
    )


def _workspace_effective_cwd_exists(path_value: str | None) -> bool | None:
    if not path_value:
        return None
    try:
        return Path(path_value).exists()
    except OSError:
        return False


def _render_workspace_bits(
    *,
    workspace_mode: str,
    workspace_health: str | None,
    workspace_label: str | None,
    original_cwd: str | None,
    effective_cwd: str | None,
    workspace_cleanup_status: str | None,
    workspace_cleanup_error: str | None = None,
    workspace_unavailable: bool = False,
    workspace_fallback_cwd: str | None = None,
) -> list[str]:
    bits = [f"workspace={workspace_mode or 'main'}"]
    if workspace_health:
        bits.append(f"health={workspace_health}")
    if workspace_label:
        bits.append(f"label={workspace_label}")
    if original_cwd:
        bits.append(f"origin={original_cwd}")
    if effective_cwd:
        bits.append(f"cwd={effective_cwd}")
        exists = _workspace_effective_cwd_exists(effective_cwd)
        if exists is False:
            bits.append("cwd_exists=no")
    cleanup_status = workspace_cleanup_status or "none"
    bits.append(f"cleanup={cleanup_status}")
    if workspace_cleanup_error:
        bits.append(f"cleanup_error={workspace_cleanup_error}")
    if workspace_unavailable:
        bits.append("unavailable=yes")
        if workspace_fallback_cwd:
            bits.append(f"fallback={workspace_fallback_cwd}")
    return bits


def _render_workspace_summary_line_from_state(state) -> str | None:
    mode = str(state.workspace_mode or "main")
    cleanup_status = str(state.workspace_cleanup_status or "none")
    bits = _render_workspace_bits(
        workspace_mode=mode,
        workspace_health=getattr(state, "workspace_health", "healthy"),
        workspace_label=state.workspace_label,
        original_cwd=state.original_cwd,
        effective_cwd=state.effective_cwd,
        workspace_cleanup_status=cleanup_status,
        workspace_cleanup_error=state.workspace_cleanup_error,
        workspace_unavailable=bool(state.workspace_unavailable),
        workspace_fallback_cwd=state.workspace_fallback_cwd,
    )
    if (
        mode == "main"
        and cleanup_status == "none"
        and "cwd_exists=no" not in bits
        and "unavailable=yes" not in bits
    ):
        return None
    return "workspace: " + "  ".join(bits)


def _workspace_recommended_actions_from_state(state) -> tuple[str, ...]:
    return workspace_recommended_actions(
        workspace_health=str(getattr(state, "workspace_health", "healthy") or "healthy"),
        workspace_label=getattr(state, "workspace_label", None),
        session_id=getattr(state, "session_id", None),
    )


def _render_workspace_guidance_lines_from_state(state) -> list[str]:
    actions = _workspace_recommended_actions_from_state(state)
    if not actions and not bool(getattr(state, "workspace_unavailable", False)):
        return []
    lines: list[str] = [
        f"workspace_health: {getattr(state, 'workspace_health', 'healthy')}",
    ]
    if bool(getattr(state, "workspace_unavailable", False)):
        expected_cwd = str(getattr(state, "effective_cwd", "") or "").strip()
        if expected_cwd:
            lines.append(f"workspace_expected_effective_cwd: {expected_cwd}")
        fallback_cwd = str(
            getattr(state, "workspace_fallback_cwd", None)
            or getattr(state, "original_cwd", None)
            or ""
        ).strip()
        if fallback_cwd:
            lines.append(f"workspace_fallback_cwd: {fallback_cwd}")
    lines.append(
        "workspace_recommended_actions: "
        + (", ".join(actions) if actions else "none")
    )
    return lines


def _render_execution_contract_bits(
    *,
    session_execution_mode: str | None,
    session_command_policy_name: str | None,
    session_command_policy_require_read_only_subagents: bool,
) -> list[str]:
    bits = [f"execution={session_execution_mode or 'main'}"]
    if session_command_policy_name:
        bits.append(f"policy={session_command_policy_name}")
    if session_command_policy_require_read_only_subagents:
        bits.append("read_only_subagents=yes")
    return bits


def _render_execution_contract_lines_from_state(state) -> list[str]:
    lines = [f"session_execution_mode: {getattr(state, 'session_execution_mode', 'main') or 'main'}"]
    lines.append(
        "session_command_policy_name: "
        + str(getattr(state, "session_command_policy_name", None) or "none")
    )
    lines.append(
        "session_command_policy_source: "
        + str(getattr(state, "session_command_policy_source", None) or "none")
    )
    allowed_tools = list(getattr(state, "session_command_policy_allowed_tool_names", []) or [])
    allowed_prefixes = list(getattr(state, "session_command_policy_allowed_bash_prefixes", []) or [])
    lines.append(
        "session_command_policy_allowed_tools: "
        + (", ".join(allowed_tools) if allowed_tools else "none")
    )
    lines.append(
        "session_command_policy_allowed_bash_prefixes: "
        + (", ".join(allowed_prefixes) if allowed_prefixes else "none")
    )
    lines.append(
        "session_command_policy_require_read_only_subagents: "
        + (
            "yes"
            if bool(getattr(state, "session_command_policy_require_read_only_subagents", False))
            else "no"
        )
    )
    return lines


def _render_execution_summary_line_from_state(state) -> str:
    bits = _render_execution_contract_bits(
        session_execution_mode=getattr(state, "session_execution_mode", "main"),
        session_command_policy_name=getattr(state, "session_command_policy_name", None),
        session_command_policy_require_read_only_subagents=bool(
            getattr(state, "session_command_policy_require_read_only_subagents", False)
        ),
    )
    return "execution: " + "  ".join(bits)


def _render_task_surface_bits(task_surface_counts: dict[str, int] | None) -> list[str]:
    if not isinstance(task_surface_counts, dict):
        return []
    counts: dict[str, int] = {}
    for key, value in task_surface_counts.items():
        try:
            counts[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    if not counts:
        return []
    total = sum(counts.values())
    non_zero = [f"{key}:{value}" for key, value in counts.items() if value > 0]
    bits = [f"tasks={total}"]
    if non_zero:
        bits.append("task_surfaces=" + ",".join(non_zero))
    return bits


def _render_planning_bits(*, active_planning_artifact_id: str | None, planning_artifact_count: int | None) -> list[str]:
    bits = [f"plans={int(planning_artifact_count or 0)}"]
    if active_planning_artifact_id:
        bits.append(f"active_plan={active_planning_artifact_id}")
    return bits


def _render_background_session_header(record: BackgroundSessionRecord, *, include_prompt: bool) -> str:
    metadata = background_session_metadata(record, detail=True)
    workflow = background_workflow_payload(record, detail=True)
    actions = background_grouped_actions(record, detail=True)
    bridge_endpoint = (
        f"{record.bridge_host}:{record.bridge_port}"
        if record.bridge_host and record.bridge_port
        else "none"
    )
    task_surface_counts = metadata["background_task_surface_counts"]
    if isinstance(task_surface_counts, dict) and task_surface_counts:
        task_surface_summary = ",".join(
            f"{key}:{int(value)}"
            for key, value in task_surface_counts.items()
            if isinstance(value, int) and value >= 0
        )
    else:
        task_surface_summary = "none"
    lines = [
        "background session:",
        f"background session id: {record.bg_id}",
        f"session id: {record.session_id or 'none'}",
        f"background session source: {metadata['background_session_source']}",
        f"continuation category: {actions['category']}",
        f"live attachable: {'yes' if metadata['background_live_attachable'] else 'no'}",
        f"saved resumable: {'yes' if metadata['background_saved_resumable'] else 'no'}",
        f"inactive only: {'yes' if metadata['background_inactive_only'] else 'no'}",
        f"primary action: {metadata['background_primary_action']}",
        f"secondary action: {metadata['background_secondary_action']}",
        f"provider: {record.provider}",
        f"model: {record.model}",
        f"status: {record.status}",
        f"created at: {record.created_at}",
        f"updated at: {record.updated_at or 'none'}",
        f"ended at: {record.ended_at or 'none'}",
        f"pid: {record.pid if record.pid is not None else 'none'}",
        f"log path: {record.log_path or 'none'}",
        f"transcript path: {metadata['background_transcript_path'] or 'none'}",
        f"last known message count: {metadata['background_last_known_message_count'] if metadata['background_last_known_message_count'] is not None else 'none'}",
        f"last known context summary chars: {metadata['background_last_known_context_summary_chars'] if metadata['background_last_known_context_summary_chars'] is not None else 'none'}",
        f"task surfaces: {task_surface_summary}",
        f"has active plan: {'yes' if metadata['background_has_active_plan'] else 'no'}",
        f"active plan id: {metadata['background_active_plan_id'] or 'none'}",
        f"planning artifact count: {int(metadata['background_planning_artifact_count'] or 0)}",
        f"workspace mode: {record.workspace_mode}",
        f"workspace health: {getattr(record, 'workspace_health', 'healthy')}",
        f"workspace label: {record.workspace_label or 'none'}",
        f"origin cwd: {record.original_cwd or record.cwd}",
        f"effective cwd: {record.effective_cwd or record.cwd}",
        f"fallback cwd: {getattr(record, 'workspace_fallback_cwd', None) or 'none'}",
        f"workspace unavailable: {'yes' if bool(getattr(record, 'workspace_unavailable', False)) else 'no'}",
        f"workspace cleanup status: {record.workspace_cleanup_status}",
        f"bridge endpoint: {bridge_endpoint}",
        f"go_to_live_attach: {actions['go_to_live_attach']}",
        f"go_to_saved_resume: {actions['go_to_saved_resume']}",
        f"stay_on_surface: {actions['stay_on_surface']}",
    ]
    workflow_lines = ["background workflow:"]
    workflow_lines.extend(
        render_summary_field_lines(
            [
                ("current workflow", workflow["background_current_workflow_summary"]),
                ("task surfaces", workflow["background_task_surface_summary"]),
                ("background execution tasks", workflow["background_background_execution_count"]),
                ("active plan execution tasks", workflow["background_active_plan_execution_count"]),
                (
                    "primary task",
                    (
                        f"{workflow['background_primary_task']['task_id']} "
                        f"({workflow['background_primary_task']['surface_kind']}: "
                        f"{workflow['background_primary_task']['description']})"
                        if workflow["background_primary_task"] is not None
                        else None
                    ),
                ),
                (
                    "primary task status",
                    (
                        workflow["background_primary_task"]["status"]
                        if workflow["background_primary_task"] is not None
                        else None
                    ),
                ),
                (
                    "primary task progress",
                    (
                        workflow["background_primary_task"]["progress_summary"]
                        if workflow["background_primary_task"] is not None
                        else None
                    ),
                ),
                ("active plan", workflow["background_active_plan_summary"]),
                ("recent changes", workflow["background_recent_change_count"]),
                ("latest change", workflow["background_latest_change_summary"]),
                ("latest change tool", workflow["background_latest_change_tool_name"]),
                ("latest change files", workflow["background_latest_change_file_count"]),
                ("recent activity", workflow["background_recent_activity"]),
                ("token count", _render_background_token_summary(workflow)),
                ("last tool", workflow["background_last_tool"]),
                ("last tool input", workflow["background_last_tool_input"]),
                ("active tool status", workflow["background_runtime_active_tool_status"]),
                (
                    "parallel batch",
                    (
                        f"active size={workflow['background_runtime_parallel_batch_size']}"
                        if workflow["background_runtime_parallel_batch_active"]
                        else "none"
                    ),
                ),
                ("last tool summary", workflow["background_last_tool_summary"]),
                ("last tool-result summary", workflow["background_runtime_last_result_summary"]),
                ("budget pressure", workflow["background_runtime_budget_pressure_summary"]),
                ("compact recovery", workflow["background_runtime_compact_recovery_summary"]),
                ("tool uses", workflow["background_tool_use_count"]),
                ("message count", workflow["background_message_count"]),
                ("progress summary", workflow["background_progress_summary"]),
                ("completion state", workflow["background_completion_state"]),
                ("completion summary", workflow["background_completion_summary"]),
                ("failure reason", workflow["background_failure_reason"]),
                ("result pointer", workflow["background_result_pointer"]),
                ("transcript pointer", workflow["background_transcript_pointer"]),
                ("pending followups", workflow["background_pending_followup_count"]),
                ("pending followup", workflow["background_pending_followup_summary"]),
                ("latest followup", workflow["background_latest_followup_message"]),
                ("latest followup mode", workflow["background_latest_followup_mode"]),
                ("working set", f"{workflow['background_working_set_file_count']} file(s)"),
                ("focused file", workflow["background_focused_file"]),
                ("focused file source", workflow["background_focused_file_source"]),
                ("explicit context entries", workflow["background_explicit_context_count"]),
            ],
            line_prefix="- ",
        )
    )
    workflow_lines.extend(
        render_workflow_action_sections(
            workflow["background_action_groups"],
            heading="next_actions:",
            ordered_keys=workflow["background_action_order"],
            line_prefix="- ",
        )
    )
    if include_prompt:
        lines.insert(9, f"prompt: {record.prompt}")
    lines.extend(["", *workflow_lines])
    return "\n".join(lines)


def _render_background_session_detail(record: BackgroundSessionRecord) -> str:
    return _render_background_session_header(record, include_prompt=True)


def _render_background_sessions(records: list[BackgroundSessionRecord]) -> str:
    if not records:
        return "No background sessions."
    lines = []
    for item in records:
        updated = item.updated_at or item.created_at
        session_id = item.session_id or "-"
        metadata = background_session_metadata(item, detail=False)
        workflow = background_workflow_payload(item, detail=False)
        workspace_bits = _render_workspace_bits(
            workspace_mode=item.workspace_mode,
            workspace_health=getattr(item, "workspace_health", "healthy"),
            workspace_label=item.workspace_label,
            original_cwd=item.original_cwd,
            effective_cwd=item.effective_cwd or item.cwd,
            workspace_cleanup_status=item.workspace_cleanup_status,
            workspace_cleanup_error=item.workspace_cleanup_error,
            workspace_unavailable=bool(getattr(item, "workspace_unavailable", False)),
            workspace_fallback_cwd=getattr(item, "workspace_fallback_cwd", None),
        )
        execution_bits = _render_execution_contract_bits(
            session_execution_mode=getattr(item, "session_execution_mode", "background-session"),
            session_command_policy_name=getattr(item, "session_command_policy_name", None),
            session_command_policy_require_read_only_subagents=bool(
                getattr(item, "session_command_policy_require_read_only_subagents", False)
            ),
        )
        source_bits = [
            "source=" + str(metadata["background_session_source"]),
            "continue=" + str(metadata["background_primary_action"]),
            "continuation=" + str(metadata["background_continuation_category"]),
            "live_attachable=" + ("yes" if metadata["background_live_attachable"] else "no"),
            "saved_resumable=" + ("yes" if metadata["background_saved_resumable"] else "no"),
            "inactive_only=" + ("yes" if metadata["background_inactive_only"] else "no"),
        ]
        message_count = metadata["background_last_known_message_count"]
        context_summary_chars = metadata["background_last_known_context_summary_chars"]
        if message_count is not None:
            source_bits.append(f"messages={int(message_count)}")
        if context_summary_chars is not None:
            source_bits.append(f"context_summary_chars={int(context_summary_chars)}")
        progress_summary = str(workflow["background_progress_summary"] or "").strip()
        if progress_summary:
            source_bits.append("progress=" + progress_summary)
        token_summary = _render_background_token_summary(workflow)
        if token_summary:
            source_bits.append("tokens=" + token_summary)
        completion_state = str(workflow["background_completion_state"] or "").strip()
        if completion_state:
            source_bits.append("completion=" + completion_state)
        pending_followups = int(workflow.get("background_pending_followup_count", 0) or 0)
        if pending_followups > 0:
            source_bits.append(f"pending_followups={pending_followups}")
        task_bits = _render_task_surface_bits(metadata["background_task_surface_counts"])
        planning_bits = _render_planning_bits(
            active_planning_artifact_id=metadata["background_active_plan_id"],
            planning_artifact_count=metadata["background_planning_artifact_count"],
        )
        lines.append(
            f"{item.bg_id}  status={item.status}  updated={updated}  "
            f"session={session_id}  provider={item.provider}  model={item.model}  "
            + "  ".join(
                [
                    *source_bits,
                    *workspace_bits,
                    *execution_bits,
                    *task_bits,
                    *planning_bits,
                    f"go_to_live_attach={metadata['background_go_to_live_attach']}",
                    f"go_to_saved_resume={metadata['background_go_to_saved_resume']}",
                    f"stay_on_surface={metadata['background_stay_on_surface']}",
                ]
            )
        )
    return "\n".join(lines)


def _render_background_token_summary(payload: dict[str, object]) -> str | None:
    token_count = payload.get("background_token_count")
    if token_count in {None, ""}:
        return None
    token_source = str(payload.get("background_token_count_source") or "").strip()
    return f"{token_count} ({token_source})" if token_source else str(token_count)


def _resolve_background_session_or_raise(cwd: Path, identifier: str) -> BackgroundSessionRecord:
    record = resolve_background_session(cwd, identifier)
    if record is None:
        raise FileNotFoundError(f'No background session found for "{identifier}".')
    return record


def _workspace_from_background_record(record: BackgroundSessionRecord) -> IsolatedWorkspace | None:
    if record.workspace_mode not in {"snapshot", "worktree"}:
        return None
    original_cwd = Path(record.original_cwd or record.cwd)
    effective_cwd = Path(record.effective_cwd or record.cwd)
    return IsolatedWorkspace(
        mode=record.workspace_mode,
        label=record.workspace_label or "restored-workspace",
        original_cwd=original_cwd.resolve(),
        effective_cwd=effective_cwd.resolve(),
        created_at=record.workspace_created_at or "",
    )


def _background_worker_argv(args, bg_id: str) -> list[str]:
    module_root = Path(__file__).resolve().parents[1]
    argv = [sys.executable, "-m", "claudecode_py.cli", "--cwd", str(args.cwd)]
    if args.provider:
        argv.extend(["--provider", args.provider])
    if args.api_key:
        argv.extend(["--api-key", args.api_key])
    if args.base_url:
        argv.extend(["--base-url", args.base_url])
    if args.mcp_config:
        argv.extend(["--mcp-config", args.mcp_config])
    if args.permission_config:
        argv.extend(["--permission-config", args.permission_config])
    if args.model:
        argv.extend(["--model", args.model])
    if args.max_turns is not None:
        argv.extend(["--max-turns", str(args.max_turns)])
    if args.max_tokens is not None:
        argv.extend(["--max-tokens", str(args.max_tokens)])
    if args.permission_mode:
        argv.extend(["--permission-mode", args.permission_mode])
    argv.extend(["_bg-runner", "--bg-id", bg_id, args.prompt])
    return argv, module_root


def _launch_background_ask(args, config) -> int:
    record = create_background_session(
        config.cwd,
        prompt=args.prompt,
        provider=config.provider,
        model=config.model,
        status="queued",
    )
    argv, module_root = _background_worker_argv(args, record.bg_id)
    log_path = get_background_session_log_path(config.cwd, record.bg_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    creationflags = 0
    popen_kwargs = {
        "cwd": str(module_root),
        "stdout": None,
        "stderr": None,
        "stdin": subprocess.DEVNULL,
        "text": True,
    }
    log_handle = log_path.open("a", encoding="utf-8")
    popen_kwargs["stdout"] = log_handle
    popen_kwargs["stderr"] = log_handle
    if os.name == "nt":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if creationflags:
            popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(argv, **popen_kwargs)
    finally:
        log_handle.close()

    update_background_session(
        config.cwd,
        record.bg_id,
        status="running",
        pid=process.pid,
    )
    print(
        f'Launched background session {record.bg_id} pid={process.pid} '
        f'log={log_path}'
    )
    return 0


def _run_background_worker(config, *, bg_id: str, prompt: str) -> int:
    workspace = prepare_isolated_workspace(config.cwd, label="background")
    workspace_health = derive_workspace_health(
        workspace_mode=workspace.mode,
        workspace_cleanup_status="pending",
        workspace_unavailable=False,
    )
    workspace_cleanup_status = "pending"
    workspace_cleanup_error: str | None = None
    worker_config = replace(
        config,
        cwd=workspace.effective_cwd,
        transcript_cwd=config.cwd,
        mcp_config_path=(
            (workspace.effective_cwd / ".pyclaude" / "mcp_servers.json").resolve()
            if config.mcp_config_path is not None
            else None
        ),
    )
    dispatcher = ServiceDispatcher(worker_config)
    created = dispatcher.handle({"id": 1, "method": "session.create", "params": {}})
    if "error" in created:
        error = created["error"]["message"]
        update_background_session(
            config.cwd,
            bg_id,
            status="failed",
            error=error,
            exit_code=1,
            original_cwd=str(config.cwd),
            effective_cwd=str(workspace.effective_cwd),
            workspace_mode=workspace.mode,
            workspace_label=workspace.label,
            workspace_created_at=workspace.created_at,
            workspace_health=workspace_health,
            workspace_cleanup_status=workspace_cleanup_status,
            workspace_cleanup_error=workspace_cleanup_error,
            workspace_unavailable=False,
            workspace_unavailable_reason=None,
            workspace_fallback_cwd=str(config.cwd.resolve()),
        )
        print(f"error: RuntimeError: {error}", flush=True)
        try:
            cleanup_isolated_workspace(workspace)
            workspace_cleanup_status = "completed"
        except Exception as exc:  # noqa: BLE001
            workspace_cleanup_status = "failed"
            workspace_cleanup_error = f"{type(exc).__name__}: {exc}"
        finally:
            update_background_session(
                config.cwd,
                bg_id,
                workspace_cleanup_status=workspace_cleanup_status,
                workspace_cleanup_error=workspace_cleanup_error,
            )
            dispatcher.close()
        return 1
    session_id = created["result"]["session_id"]
    session = dispatcher._sessions[session_id].session
    session.set_background_session_link(bg_id)
    session.set_session_execution_contract(execution_mode="background-session")
    session.state.original_cwd = str(config.cwd.resolve())
    session.state.effective_cwd = str(workspace.effective_cwd.resolve())
    session.state.workspace_mode = workspace.mode
    session.state.workspace_label = workspace.label
    session.state.workspace_created_at = workspace.created_at
    session.state.workspace_health = workspace_health
    session.state.workspace_cleanup_status = workspace_cleanup_status
    session.state.workspace_cleanup_error = workspace_cleanup_error
    transcript_path = get_session_path(config.cwd, session_id)
    server = BridgeTcpServer("127.0.0.1", 0, dispatcher)
    host, port = server.server_address
    update_background_session(
        config.cwd,
        bg_id,
        status="busy",
        session_id=session_id,
        transcript_path=str(transcript_path),
        bridge_host=str(host),
        bridge_port=int(port),
        original_cwd=str(config.cwd),
        effective_cwd=str(workspace.effective_cwd),
        workspace_mode=workspace.mode,
        workspace_label=workspace.label,
        workspace_created_at=workspace.created_at,
        workspace_health=workspace_health,
        workspace_cleanup_status=workspace_cleanup_status,
        workspace_cleanup_error=workspace_cleanup_error,
        workspace_unavailable=False,
        workspace_unavailable_reason=None,
        workspace_fallback_cwd=str(config.cwd.resolve()),
        session_execution_mode=session.state.session_execution_mode,
        session_command_policy_name=session.state.session_command_policy_name,
        session_command_policy_source=session.state.session_command_policy_source,
        session_command_policy_allowed_tool_names=list(session.state.session_command_policy_allowed_tool_names),
        session_command_policy_allowed_bash_prefixes=list(session.state.session_command_policy_allowed_bash_prefixes),
        session_command_policy_require_read_only_subagents=(
            session.state.session_command_policy_require_read_only_subagents
        ),
    )

    def run_initial_prompt() -> None:
        response = dispatcher.handle(
            {
                "id": 2,
                "method": "session.ask",
                "params": {"session_id": session_id, "prompt": prompt},
            }
        )
        if "error" in response:
            error = response["error"]["message"]
            print(f"error: RuntimeError: {error}", flush=True)
            update_background_session(
                config.cwd,
                bg_id,
                status="failed",
                session_id=session_id,
                transcript_path=str(transcript_path),
                error=error,
                exit_code=1,
                original_cwd=str(config.cwd),
                effective_cwd=str(workspace.effective_cwd),
                workspace_mode=workspace.mode,
                workspace_label=workspace.label,
                workspace_created_at=workspace.created_at,
                workspace_health=workspace_health,
                workspace_cleanup_status=workspace_cleanup_status,
                workspace_cleanup_error=workspace_cleanup_error,
                workspace_unavailable=False,
                workspace_unavailable_reason=None,
                workspace_fallback_cwd=str(config.cwd.resolve()),
                session_execution_mode=session.state.session_execution_mode,
                session_command_policy_name=session.state.session_command_policy_name,
                session_command_policy_source=session.state.session_command_policy_source,
                session_command_policy_allowed_tool_names=list(session.state.session_command_policy_allowed_tool_names),
                session_command_policy_allowed_bash_prefixes=list(
                    session.state.session_command_policy_allowed_bash_prefixes
                ),
                session_command_policy_require_read_only_subagents=(
                    session.state.session_command_policy_require_read_only_subagents
                ),
            )
            return
        payload = response["result"]["payload"]
        output = payload.get("output") or ""
        if output:
            print(output, flush=True)
        update_background_session(
            config.cwd,
            bg_id,
            status="running",
            session_id=session_id,
            transcript_path=payload.get("transcript_path") or str(transcript_path),
            exit_code=0,
            original_cwd=str(config.cwd),
            effective_cwd=str(workspace.effective_cwd),
            workspace_mode=workspace.mode,
            workspace_label=workspace.label,
            workspace_created_at=workspace.created_at,
            workspace_health=workspace_health,
            workspace_cleanup_status=workspace_cleanup_status,
            workspace_cleanup_error=workspace_cleanup_error,
            workspace_unavailable=False,
            workspace_unavailable_reason=None,
            workspace_fallback_cwd=str(config.cwd.resolve()),
        )

    prompt_thread = Thread(target=run_initial_prompt, daemon=True)
    prompt_thread.start()
    try:
        server.serve_forever()
        return 0
    except KeyboardInterrupt:
        update_background_session(
            config.cwd,
            bg_id,
            status="stopped",
            exit_code=0,
            workspace_cleanup_status=workspace_cleanup_status,
            workspace_cleanup_error=workspace_cleanup_error,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        update_background_session(
            config.cwd,
            bg_id,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            exit_code=1,
            workspace_cleanup_status=workspace_cleanup_status,
            workspace_cleanup_error=workspace_cleanup_error,
        )
        print(f"error: {type(exc).__name__}: {exc}", flush=True)
        return 1
    finally:
        server.close()
        dispatcher.close()
        try:
            cleanup_isolated_workspace(workspace)
            workspace_cleanup_status = "completed"
            workspace_cleanup_error = None
            workspace_health = derive_workspace_health(
                workspace_mode=workspace.mode,
                workspace_cleanup_status=workspace_cleanup_status,
                workspace_unavailable=False,
            )
        except Exception as exc:  # noqa: BLE001
            workspace_cleanup_status = "failed"
            workspace_cleanup_error = f"{type(exc).__name__}: {exc}"
            workspace_health = derive_workspace_health(
                workspace_mode=workspace.mode,
                workspace_cleanup_status=workspace_cleanup_status,
                workspace_unavailable=False,
            )
        finally:
            update_background_session(
                config.cwd,
                bg_id,
                workspace_health=workspace_health,
                workspace_cleanup_status=workspace_cleanup_status,
                workspace_cleanup_error=workspace_cleanup_error,
            )


def _print_background_session_log(record: BackgroundSessionRecord, *, follow: bool, summary_only: bool = False) -> int:
    print(_render_background_session_header(record, include_prompt=False))
    if summary_only:
        return 0
    log_path = Path(record.log_path) if record.log_path else None
    if log_path is None or not log_path.exists():
        print("No log output available.")
        return 0

    with log_path.open("r", encoding="utf-8") as handle:
        print()
        content = handle.read()
        if content:
            print(content, end="" if content.endswith("\n") else "\n")
        if not follow:
            return 0
        while True:
            refreshed = resolve_background_session(Path(record.cwd), record.bg_id) or record
            chunk = handle.read()
            if chunk:
                print(chunk, end="" if chunk.endswith("\n") else "\n")
            if refreshed.status in {"completed", "failed", "stopped"}:
                return 0
            sleep(0.2)


def _build_remote_background_session(record: BackgroundSessionRecord) -> RemoteSessionProxy:
    if not record.bridge_host or not record.bridge_port or not record.session_id:
        if record.session_id and record.status in {"completed", "failed", "stopped"}:
            raise RuntimeError(
                "Background session is no longer live. "
                f'Resume saved state with "pyclaude --resume-session {record.session_id} repl".'
            )
        raise RuntimeError("Background session does not expose a live bridge endpoint.")
    return RemoteSessionProxy(
        host=str(record.bridge_host),
        port=int(record.bridge_port),
        session_id=str(record.session_id),
    )


def _run_attached_repl(session, *, bg_id: str) -> int:
    renderer = _ConsoleEventRenderer()
    session.set_live_event_sink(renderer)
    active_request: dict[str, Thread | None] = {"thread": None}

    def launch_request(func) -> None:
        current = active_request["thread"]
        if current is not None and current.is_alive():
            print("Session is busy. Resolve the pending work or approval before sending another prompt.")
            return

        def worker() -> None:
            try:
                func()
            except Exception as exc:  # noqa: BLE001
                renderer.finish()
                print(f"error: {type(exc).__name__}: {exc}")
            finally:
                active_request["thread"] = None
                renderer.finish()

        thread = Thread(target=worker, daemon=True)
        active_request["thread"] = thread
        thread.start()

    if hasattr(session, "set_approval_handlers"):
        session.set_approval_handlers(
            lambda request: print(
                f'\n[approval] {request.tool_name} ({request.risk_level}) requires approval.'
                '\nUse /approve, /approve-session, or /deny.'
                + (f"\n{request.details}" if request.details else "")
            ),
            lambda result: print(
                f"\n[approval] resolved: {result.decision} ({result.scope})"
            ),
        )
    try:
        for event in session.take_replay_events():
            renderer(event)
        renderer.finish()
        print(f"PyClaudeCode REPL attached to background session {bg_id}")
        if session.state.messages:
            print(f'Attached to live session {session.state.session_id} with {len(session.state.messages)} messages.')
        for line in _session_source_header_lines(
            session,
            session_source="live_background",
            live_background_id=bg_id,
        ):
            print(line)
        workspace_summary = _render_workspace_summary_line_from_state(session.state)
        if workspace_summary:
            print(workspace_summary)
        print(_render_execution_summary_line_from_state(session.state))
        for line in _render_workspace_guidance_lines_from_state(session.state):
            print(line)
        for line in _render_execution_contract_lines_from_state(session.state):
            print(line)
        if hasattr(session, "task_surface_summary_lines"):
            for line in session.task_surface_summary_lines():
                print(line)
        if getattr(session, "pending_approval", None) is not None:
            request = session.pending_approval
            print(
                f'[approval] Pending request for {request.tool_name} ({request.risk_level}). '
                "Use /approve, /approve-session, or /deny."
            )
        print('Type "/exit" to detach.')
        while True:
            try:
                prompt = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not prompt:
                continue
            if prompt == "/exit":
                current = active_request["thread"]
                if current is not None and current.is_alive():
                    current.join(timeout=1.0)
                return 0
            handled, output = _handle_repl_command(session, prompt)
            if handled:
                if isinstance(output, CommandExecution):
                    launch_request(lambda: session.run_command(output))
                    continue
                renderer.finish()
                if output:
                    print(output)
                continue
            launch_request(lambda: session.ask(prompt))
    finally:
        session.set_live_event_sink(None)
        if hasattr(session, "set_approval_handlers"):
            session.set_approval_handlers(None, None)


def _attach_background_session(record: BackgroundSessionRecord, *, mode: str = "repl") -> int:
    session = _build_remote_background_session(record)
    try:
        if mode == "tui":
            return run_tui(session, session_source="live_background", live_background_id=record.bg_id)
        return _run_attached_repl(session, bg_id=record.bg_id)
    finally:
        session.close()


def _terminate_background_session(record: BackgroundSessionRecord) -> None:
    if record.pid is None:
        raise RuntimeError("Background session has no tracked process id.")
    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill", "/PID", str(record.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "taskkill failed"
            raise RuntimeError(detail)
        return
    os.kill(record.pid, signal.SIGTERM)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_dotenv(Path(args.cwd) / ".env")
    config = load_config(
        cwd=args.cwd,
        provider=args.provider,
        api_key=args.api_key,
        base_url=args.base_url,
        mcp_config_path=args.mcp_config,
        permission_config_path=args.permission_config,
        model=args.model,
        max_tokens=args.max_tokens,
        max_turns=args.max_turns,
        permission_mode=args.permission_mode,
        interactive=args.command in {"repl", "tui"},
    )
    session_factory = SessionFactory(load_mcp_from_config=True)

    if args.command == "sessions":
        summaries = list_transcripts(config.cwd, limit=args.limit)
        if not summaries:
            print("No saved sessions.")
            return 0
        for item in summaries:
            updated = item.updated_at or item.created_at or "unknown"
            provider = item.provider or "unknown"
            model = item.model or "unknown"
            compacted = "yes" if item.context_summary_present else "no"
            workspace_health = getattr(item, "workspace_health", None) or derive_workspace_health(
                workspace_mode=item.workspace_mode or "main",
                workspace_cleanup_status=item.workspace_cleanup_status or "none",
                workspace_unavailable=bool(getattr(item, "workspace_unavailable", False)),
            )
            workspace_bits = _render_workspace_bits(
                workspace_mode=item.workspace_mode or "main",
                workspace_health=workspace_health,
                workspace_label=item.workspace_label,
                original_cwd=item.original_cwd or item.cwd,
                effective_cwd=item.effective_cwd or item.cwd,
                workspace_cleanup_status=item.workspace_cleanup_status or "none",
                workspace_unavailable=bool(getattr(item, "workspace_unavailable", False)),
                workspace_fallback_cwd=getattr(item, "workspace_fallback_cwd", None),
            )
            recommended_actions = workspace_recommended_actions(
                workspace_health=str(workspace_health or "healthy"),
                workspace_label=item.workspace_label,
                session_id=item.session_id,
            )
            if recommended_actions:
                workspace_bits.append("actions=" + " | ".join(recommended_actions))
            execution_bits = _render_execution_contract_bits(
                session_execution_mode=getattr(item, "session_execution_mode", "main"),
                session_command_policy_name=getattr(item, "session_command_policy_name", None),
                session_command_policy_require_read_only_subagents=bool(
                    getattr(item, "session_command_policy_require_read_only_subagents", False)
                ),
            )
            planning_bits = _render_planning_bits(
                active_planning_artifact_id=getattr(item, "active_planning_artifact_id", None),
                planning_artifact_count=getattr(item, "planning_artifact_count", 0),
            )
            task_bits = _render_task_surface_bits(getattr(item, "task_surface_counts", {}))
            print(
                f"{item.session_id}  updated={updated}  provider={provider}  "
                f"model={model}  source=saved  continue=pyclaude --resume-session {item.session_id} repl  "
                f"messages={item.message_count}  compacted={compacted}  "
                + "  ".join([*planning_bits, *task_bits, *workspace_bits, *execution_bits])
            )
        return 0

    if args.command == "agents":
        session = session_factory.create_session(config)
        try:
            print(session.describe_agents())
            return 0
        finally:
            session.close()

    if args.command == "ps":
        if args.session:
            try:
                record = _resolve_background_session_or_raise(config.cwd, args.session)
            except FileNotFoundError as exc:
                print(f"error: FileNotFoundError: {exc}")
                return 1
            print(_render_background_session_detail(record))
            return 0
        print(_render_background_sessions(list_background_sessions(config.cwd)[: args.limit]))
        return 0

    if args.command in {"logs", "attach", "kill"}:
        try:
            record = _resolve_background_session_or_raise(config.cwd, args.session)
        except FileNotFoundError as exc:
            print(f"error: FileNotFoundError: {exc}")
            return 1
        if args.command == "logs":
            return _print_background_session_log(record, follow=False, summary_only=args.view == "summary")
        if args.command == "attach":
            try:
                return _attach_background_session(record, mode=args.mode)
            except Exception as exc:  # noqa: BLE001
                print(f"error: {type(exc).__name__}: {exc}")
                return 1
        try:
            if record.status in {"completed", "failed", "stopped"}:
                print(f'Background session "{record.bg_id}" is already {record.status}.')
                return 0
            _terminate_background_session(record)
            workspace = _workspace_from_background_record(record)
            workspace_cleanup_status = record.workspace_cleanup_status
            workspace_cleanup_error = record.workspace_cleanup_error
            workspace_health = getattr(record, "workspace_health", "healthy")
            if workspace is not None:
                cleanup_isolated_workspace(workspace)
                workspace_cleanup_status = "completed"
                workspace_cleanup_error = None
                workspace_health = derive_workspace_health(
                    workspace_mode=record.workspace_mode,
                    workspace_cleanup_status=workspace_cleanup_status,
                    workspace_unavailable=bool(getattr(record, "workspace_unavailable", False)),
                )
            update_background_session(
                config.cwd,
                record.bg_id,
                status="stopped",
                exit_code=1,
                workspace_health=workspace_health,
                workspace_cleanup_status=workspace_cleanup_status,
                workspace_cleanup_error=workspace_cleanup_error,
            )
            print(f'Stopped background session "{record.bg_id}".')
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"error: {type(exc).__name__}: {exc}")
            return 1

    if args.command == "_bg-runner":
        return _run_background_worker(config, bg_id=args.bg_id, prompt=args.prompt)

    if args.command == "locate-symbol":
        try:
            result = locate_symbol_headless(
                args.symbol,
                config=config,
                path=args.path,
                max_results=args.max_results,
                resume_session_id=args.resume_session,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"error: {type(exc).__name__}: {exc}")
            return 1
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        elif not result.lookup.matches:
            print(f'No symbol definitions found for "{args.symbol}".')
        else:
            for item in result.lookup.matches:
                owner = f"{item.owner}." if item.owner else ""
                print(f"{item.path}:{item.line}:{owner}{item.kind} {item.symbol}")
        return 0

    if args.command == "references":
        try:
            result = collect_references_headless(
                args.symbol,
                config=config,
                path=args.path,
                scope=args.scope,
                max_results=args.max_results,
                resume_session_id=args.resume_session,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"error: {type(exc).__name__}: {exc}")
            return 1
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        elif not result.lookup.references:
            print(f'No references found for "{args.symbol}".')
        else:
            for item in result.lookup.references:
                print(f"{item.path}:{item.line}:{item.text}")
        return 0

    if args.command == "open-file":
        try:
            result = open_file_target_headless(
                args.path,
                config=config,
                line=args.line,
                column=args.column,
                end_line=args.end_line,
                end_column=args.end_column,
                label=args.label,
                resume_session_id=args.resume_session,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"error: {type(exc).__name__}: {exc}")
            return 1
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(
                f"{result.target.path}:{result.target.line}:{result.target.column}"
                + (f" label={result.target.label}" if result.target.label else "")
            )
        return 0

    if args.command == "open-symbol":
        try:
            result = open_symbol_target_headless(
                args.symbol,
                config=config,
                path=args.path,
                match_index=args.match_index,
                resume_session_id=args.resume_session,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"error: {type(exc).__name__}: {exc}")
            return 1
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(
                f"{result.target.path}:{result.target.line}:{result.target.column}"
                + (f" label={result.target.label}" if result.target.label else "")
            )
        return 0

    if args.command == "diff-targets":
        try:
            before = Path(args.before_file).read_text(encoding="utf-8")
            after = Path(args.after_file).read_text(encoding="utf-8")
            result = diff_targets_headless(
                args.path,
                before=before,
                after=after,
                config=config,
                resume_session_id=args.resume_session,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"error: {type(exc).__name__}: {exc}")
            return 1
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            if not result.diff.hunks:
                print(f"No diff hunks for {result.diff.path}.")
            else:
                for item in result.diff.hunks:
                    print(f"{item.path}:{item.line}-{item.end_line or item.line} {item.label}")
        return 0

    if args.command == "reference-targets":
        try:
            result = reference_targets_headless(
                args.symbol,
                config=config,
                path=args.path,
                scope=args.scope,
                max_results=args.max_results,
                resume_session_id=args.resume_session,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"error: {type(exc).__name__}: {exc}")
            return 1
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        elif not result.targets.targets:
            print(f'No reference targets found for "{args.symbol}".')
        else:
            for item in result.targets.targets:
                print(f"{item.path}:{item.line}:{item.column} {item.label}")
        return 0

    if args.command == "symbol-actions":
        try:
            result = symbol_actions_headless(
                args.symbol,
                config=config,
                path=args.path,
                scope=args.scope,
                max_definition_results=args.max_definition_results,
                max_reference_results=args.max_reference_results,
                resume_session_id=args.resume_session,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"error: {type(exc).__name__}: {exc}")
            return 1
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            if not result.bundle.definitions and not result.bundle.references:
                print(f'No symbol actions found for "{args.symbol}".')
            else:
                for item in result.bundle.definitions:
                    print(f"def {item.path}:{item.line}:{item.column} {item.label}")
                for item in result.bundle.references:
                    print(f"ref {item.path}:{item.line}:{item.column} {item.label}")
        return 0

    if args.command == "mcp-call":
        try:
            parsed_args = json.loads(args.args)
        except json.JSONDecodeError as exc:
            print(f"error: JSONDecodeError: {exc.msg}")
            return 1
        if not isinstance(parsed_args, dict):
            print("error: ValueError: --args must decode to a JSON object")
            return 1
        session = session_factory.create_session(config)
        try:
            result = session.diagnose_mcp_tool(args.server, args.tool, arguments=parsed_args)
            if args.json:
                print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(session.describe_mcp_tool_diagnostic(args.server, args.tool, arguments=parsed_args))
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"error: {type(exc).__name__}: {exc}")
            return 1
        finally:
            session.close()

    if args.command == "mcp-verify":
        try:
            parsed_args = json.loads(args.args)
        except json.JSONDecodeError as exc:
            print(f"error: JSONDecodeError: {exc.msg}")
            return 1
        if not isinstance(parsed_args, dict):
            print("error: ValueError: --args must decode to a JSON object")
            return 1
        session = session_factory.create_session(config)
        try:
            result = session.verify_mcp_tool_via_model(args.server, args.tool, arguments=parsed_args)
            if args.json:
                print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(session.describe_mcp_verification(args.server, args.tool, arguments=parsed_args))
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"error: {type(exc).__name__}: {exc}")
            return 1
        finally:
            session.close()

    if args.command == "ask":
        if args.background:
            try:
                return _launch_background_ask(args, config)
            except Exception as exc:  # noqa: BLE001
                print(f"error: {type(exc).__name__}: {exc}")
                return 1
        try:
            result = run_headless(
                args.prompt,
                config=config,
                restore_latest=False,
                resume_session_id=args.resume_session,
            )
            renderer = _ConsoleEventRenderer(suppress_assistant_text=True)
            for event in result.events:
                renderer(event)
            renderer.finish()
            if result.output:
                print(result.output)
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"error: {type(exc).__name__}: {exc}")
            return 1

    if args.command == "serve-stdio":
        dispatcher = ServiceDispatcher(config)
        service = JsonRpcStdioService(dispatcher, stdin=sys.stdin, stdout=sys.stdout)
        try:
            return service.serve_forever()
        except Exception as exc:  # noqa: BLE001
            print(f"error: {type(exc).__name__}: {exc}")
            return 1

    if args.command == "serve-bridge":
        dispatcher = ServiceDispatcher(config)
        server = BridgeTcpServer(args.host, args.port, dispatcher)
        try:
            server.serve_forever()
            return 0
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"error: {type(exc).__name__}: {exc}")
            return 1
        finally:
            server.close()

    try:
        session, restored_from = session_factory.create_or_restore_session(
            config,
            restore_latest=args.command in {"repl", "tui"} and args.resume_session is None,
            resume_session_id=args.resume_session,
        )
    except FileNotFoundError as exc:
        print(f"error: FileNotFoundError: {exc}")
        return 1

    if args.command == "repl":
        try:
            if restored_from is not None and not session.state.messages:
                print(f"Loaded session from {restored_from}")
            return run_repl(
                session,
                session_source="restored_saved" if restored_from is not None else "new",
                restored_from=restored_from,
            )
        finally:
            session.close()

    if args.command == "tui":
        try:
            return run_tui(
                session,
                session_source="restored_saved" if restored_from is not None else "new",
                restored_from=restored_from,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"error: {type(exc).__name__}: {exc}")
            return 1
        finally:
            session.close()

    parser.error(f"unknown command: {args.command}")
    return 2
