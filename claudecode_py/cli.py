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
from .session import Session
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
from .workspace import IsolatedWorkspace, cleanup_isolated_workspace, prepare_isolated_workspace

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
    bg_ps = subparsers.add_parser("ps", help="List detached background sessions")
    bg_ps.add_argument("--limit", type=int, default=20, help="Maximum background sessions to show")
    bg_logs = subparsers.add_parser("logs", help="Print background session logs")
    bg_logs.add_argument("session", help="Background session id or prefix")
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


def run_repl(session: Session) -> int:
    print(f"PyClaudeCode REPL in {session.config.cwd}")
    if session.state.messages:
        print(
            f'Restored session {session.state.session_id} with {len(session.state.messages)} messages.'
        )
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


def _launch_tui(session: Session) -> int:
    try:
        from .tui import run_tui_app
    except ImportError as exc:
        if exc.name == "textual" or "textual" in str(exc):
            raise RuntimeError('Missing dependency "textual". Install with: pip install -e .[tui]') from exc
        raise
    return run_tui_app(session)


def run_tui(session: Session) -> int:
    return _launch_tui(session)


def _render_background_sessions(records: list[BackgroundSessionRecord]) -> str:
    if not records:
        return "No background sessions."
    lines = []
    for item in records:
        updated = item.updated_at or item.created_at
        session_id = item.session_id or "-"
        lines.append(
            f"{item.bg_id}  status={item.status}  updated={updated}  "
            f"session={session_id}  provider={item.provider}  model={item.model}  "
            f"workspace={item.workspace_mode}  cwd={item.effective_cwd or item.cwd}"
        )
    return "\n".join(lines)


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
        original_cwd=original_cwd.resolve(),
        effective_cwd=effective_cwd.resolve(),
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
        update_background_session(config.cwd, bg_id, status="failed", error=error, exit_code=1)
        print(f"error: RuntimeError: {error}", flush=True)
        cleanup_isolated_workspace(workspace)
        return 1
    session_id = created["result"]["session_id"]
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
        )

    prompt_thread = Thread(target=run_initial_prompt, daemon=True)
    prompt_thread.start()
    try:
        server.serve_forever()
        return 0
    except KeyboardInterrupt:
        update_background_session(config.cwd, bg_id, status="stopped", exit_code=0)
        return 0
    except Exception as exc:  # noqa: BLE001
        update_background_session(
            config.cwd,
            bg_id,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            exit_code=1,
        )
        print(f"error: {type(exc).__name__}: {exc}", flush=True)
        return 1
    finally:
        server.close()
        dispatcher.close()
        cleanup_isolated_workspace(workspace)


def _print_background_session_log(record: BackgroundSessionRecord, *, follow: bool) -> int:
    log_path = Path(record.log_path) if record.log_path else None
    if log_path is None or not log_path.exists():
        print("No log output available.")
        return 0

    with log_path.open("r", encoding="utf-8") as handle:
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
            print(
                f'Restored session {session.state.session_id} with {len(session.state.messages)} messages.'
            )
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
            return run_tui(session)
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
            print(
                f"{item.session_id}  updated={updated}  provider={provider}  "
                f"model={model}  messages={item.message_count}  compacted={compacted}"
            )
        return 0

    if args.command == "ps":
        print(_render_background_sessions(list_background_sessions(config.cwd)[: args.limit]))
        return 0

    if args.command in {"logs", "attach", "kill"}:
        try:
            record = _resolve_background_session_or_raise(config.cwd, args.session)
        except FileNotFoundError as exc:
            print(f"error: FileNotFoundError: {exc}")
            return 1
        if args.command == "logs":
            return _print_background_session_log(record, follow=False)
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
            if workspace is not None:
                cleanup_isolated_workspace(workspace)
            update_background_session(config.cwd, record.bg_id, status="stopped", exit_code=1)
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
            return run_repl(session)
        finally:
            session.close()

    if args.command == "tui":
        try:
            return run_tui(session)
        except Exception as exc:  # noqa: BLE001
            print(f"error: {type(exc).__name__}: {exc}")
            return 1
        finally:
            session.close()

    parser.error(f"unknown command: {args.command}")
    return 2
