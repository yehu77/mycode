from __future__ import annotations

import json

from .local_commands import (
    handle_add_dir_command,
    handle_changes_command,
    handle_compact_command,
    handle_config_command,
    handle_context_command,
    handle_diff_command,
    handle_files_command,
    handle_history_command,
    handle_model_command,
    handle_permissions_command,
    handle_plan_command,
    handle_project_context_command,
    handle_sessions_command,
    handle_status_command,
    handle_symbol_command,
    handle_tasks_command,
    handle_workspaces_command,
)

from .registry import CommandRegistry, ReplCommand


def build_core_commands() -> list[ReplCommand]:
    return [
        ReplCommand("/help", "Show this help text", lambda session, args: ""),
        ReplCommand("/tools", "List available tools", lambda session, args: session.describe_tools()),
        ReplCommand(
            "/model",
            "Show provider/model capability summary",
            lambda session, args: handle_model_command(session, args),
        ),
        ReplCommand(
            "/config",
            "Show current session configuration",
            lambda session, args: handle_config_command(session, args),
        ),
        ReplCommand(
            "/history",
            "Show recent conversation history",
            lambda session, args: handle_history_command(session, args),
        ),
        ReplCommand(
            "/compact",
            "Compact older conversation history into context summary, optionally with instructions",
            lambda session, args: handle_compact_command(session, args),
        ),
        ReplCommand(
            "/status",
            "Show current local session overview",
            lambda session, args: handle_status_command(session, args),
        ),
        ReplCommand(
            "/context",
            "Show current context usage",
            lambda session, args: handle_context_command(session, args),
        ),
        ReplCommand(
            "/add-dir",
            "Add or inspect explicit local context paths",
            lambda session, args: handle_add_dir_command(session, args),
        ),
        ReplCommand(
            "/files",
            "Inspect current file/workingset context",
            lambda session, args: handle_files_command(session, args),
        ),
        ReplCommand(
            "/diff",
            "Inspect current diff-backed local work",
            lambda session, args: handle_diff_command(session, args),
        ),
        ReplCommand(
            "/project-context",
            "Inspect current project memory, skills, plugins, and reload state",
            lambda session, args: handle_project_context_command(session, args),
        ),
        ReplCommand(
            "/changes",
            "Show recent workspace changes recorded for undo",
            lambda session, args: handle_changes_command(session, args),
        ),
        ReplCommand(
            "/sessions",
            "Show saved sessions in the workspace",
            lambda session, args: handle_sessions_command(session, args),
        ),
        ReplCommand(
            "/tasks",
            "Show background tasks",
            lambda session, args: handle_tasks_command(session, args),
        ),
        ReplCommand(
            "/workspaces",
            "Inspect isolated workspace health, cleanup, or repair actions",
            lambda session, args: handle_workspaces_command(session, args),
        ),
        ReplCommand(
            "/symbol",
            "Inspect current symbol surface or resolve locate/references/actions targets",
            lambda session, args: handle_symbol_command(session, args),
        ),
        ReplCommand(
            "/task",
            "Show task detail: /task show <id>",
            _task_command,
        ),
        ReplCommand(
            "/plan",
            "Inspect or manage planning artifacts",
            lambda session, args: handle_plan_command(session, args),
        ),
        ReplCommand(
            "/permissions",
            "Inspect or manage permission rules",
            lambda session, args: handle_permissions_command(session, args),
        ),
        ReplCommand(
            "/mcp",
            "Show configured MCP servers",
            lambda session, args: session.describe_mcp_servers(),
        ),
        ReplCommand(
            "/mcp-tools",
            "Show loaded MCP tools",
            lambda session, args: session.describe_mcp_tools(),
        ),
        ReplCommand(
            "/mcp-refresh",
            "Reload MCP config and refresh connected tools",
            lambda session, args: session.reload_mcp_from_config(),
        ),
        ReplCommand(
            "/mcp-reconnect",
            "Reconnect a specific MCP server by name",
            lambda session, args: session.reconnect_mcp_server(args),
        ),
        ReplCommand(
            "/mcp-call",
            "Call an MCP tool directly: /mcp-call <server> <tool> [json-args]",
            _mcp_call,
        ),
        ReplCommand(
            "/mcp-verify",
            "Verify MCP end-to-end via the model: /mcp-verify <server> <tool> [json-args]",
            _mcp_verify,
        ),
        ReplCommand(
            "/memory",
            "Show loaded project memory",
            lambda session, args: session.describe_project_memory(),
        ),
        ReplCommand(
            "/skills",
            "Show loaded project skills",
            lambda session, args: session.describe_loaded_skills(),
        ),
        ReplCommand(
            "/skills-enable",
            "Enable a loaded project skill by name",
            _enable_skill,
        ),
        ReplCommand(
            "/skills-disable",
            "Disable a loaded project skill by name",
            _disable_skill,
        ),
        ReplCommand(
            "/skills-reload",
            "Reload project memory and skills from disk",
            lambda session, args: session.reload_project_context(),
        ),
        ReplCommand(
            "/plugins",
            "Show built-in plugin status",
            lambda session, args: session.describe_plugins(),
        ),
        ReplCommand(
            "/plugin",
            "Inspect or manage plugins: /plugin [list|show <name>|enable <name>|disable <name>]",
            _plugin_command,
        ),
        ReplCommand(
            "/plugin-enable",
            "Enable a built-in plugin by name",
            lambda session, args: session.enable_plugin(args),
        ),
        ReplCommand(
            "/plugin-disable",
            "Disable a built-in plugin by name",
            lambda session, args: session.disable_plugin(args),
        ),
        ReplCommand(
            "/clear",
            "Clear local session state or start a fresh local session: /clear [history|changes|symbol|plan|session]",
            _clear_command,
        ),
        ReplCommand(
            "/undo",
            "Undo recent workspace change(s) by count or change id",
            lambda session, args: session.undo_last_change(args),
        ),
        ReplCommand(
            "/redo",
            "Redo undone workspace change(s) by count or change id",
            lambda session, args: session.redo_last_undo(args),
        ),
    ]


def build_core_command_registry() -> CommandRegistry:
    registry = CommandRegistry()
    for command in _bind_help_command(build_core_commands(), registry):
        registry.add_command(command)
    return registry


def build_default_command_registry() -> CommandRegistry:
    from ..plugins import build_builtin_plugin_registry
    from ..state import SessionState

    registry = build_core_command_registry()
    for plugin in build_builtin_plugin_registry().enabled_plugins(SessionState()):
        for command in plugin.commands:
            registry.add_command(command)
    return registry


def _bind_help_command(commands: list[ReplCommand], registry: CommandRegistry) -> list[ReplCommand]:
    bound: list[ReplCommand] = []
    for command in commands:
        if command.name == "/help":
            bound.append(
                ReplCommand(
                    command.name,
                    command.description,
                    lambda session, args: registry.render_help(),
                )
            )
            continue
        bound.append(command)
    return bound


def _clear_command(session: "Session", args: str) -> str:
    raw = args.strip().lower()
    if not raw or raw == "history":
        session.clear_history()
        return "Cleared conversation history only for this session."
    if raw == "changes":
        return session.clear_change_history()
    if raw == "symbol":
        return session.clear_symbol_surface()
    if raw == "plan":
        return session.clear_active_plan()
    if raw == "session":
        return str(session.clear_session_reset().get("text", ""))
    return "Usage: /clear [history|changes|symbol|plan|session]"


def _task_command(session: "Session", args: str) -> str:
    raw = args.strip()
    tokens = raw.split()
    if len(tokens) < 2:
        return "Usage: /task show <id> [file <n>] | /task advisor <id> | /task drift <id>"
    verb = tokens[0].lower()
    target = tokens[1].strip()
    if not target:
        return "Usage: /task show <id> [file <n>] | /task advisor <id> | /task drift <id>"
    if verb == "show":
        if len(tokens) == 2:
            session.remember_task_context_focus(target, file_index=0, preserve_current_focus=True)
            return session.describe_task_detail(target, preserve_current_focus=True)
        if len(tokens) == 4 and tokens[2].lower() == "file" and tokens[3].isdigit() and int(tokens[3]) > 0:
            task = session.resolve_task(target)
            checklist_task = session.resolve_checklist_task(target) if task is None else None
            if task is None and checklist_task is None:
                return session.describe_task_detail(target)
            payload = session.task_file_context_payload(target)
            file_count = int(payload.get("file_context_file_count") or 0) if isinstance(payload, dict) else 0
            selected_index = int(tokens[3]) - 1
            if file_count <= 0 or selected_index >= file_count:
                return "Usage: /task show <id> [file <n>] | /task advisor <id> | /task drift <id>"
            session.remember_task_context_focus(target, file_index=selected_index, preserve_current_focus=False)
            return session.describe_task_detail(target, file_index=selected_index, preserve_current_focus=False)
        return "Usage: /task show <id> [file <n>] | /task advisor <id> | /task drift <id>"
    if len(tokens) != 2:
        return "Usage: /task show <id> [file <n>] | /task advisor <id> | /task drift <id>"
    if verb == "advisor":
        session.remember_task_context_focus(target, file_index=0, preserve_current_focus=True)
        return session.open_task_detail_advisor(target)
    if verb == "drift":
        session.remember_task_context_focus(target, file_index=0, preserve_current_focus=True)
        return session.open_task_drift_detail(target)
    return "Usage: /task show <id> [file <n>] | /task advisor <id> | /task drift <id>"


def _enable_skill(session: "Session", args: str) -> str:
    return session.enable_skill(args)


def _disable_skill(session: "Session", args: str) -> str:
    return session.disable_skill(args)


def _mcp_call(session: "Session", args: str) -> str:
    raw = args.strip()
    if not raw:
        return "Usage: /mcp-call <server> <tool> [json-args]"
    parts = raw.split(maxsplit=2)
    if len(parts) < 2:
        return "Usage: /mcp-call <server> <tool> [json-args]"
    server_name, tool_name = parts[0], parts[1]
    arguments: dict | None = None
    if len(parts) == 3:
        try:
            parsed = json.loads(parts[2])
        except json.JSONDecodeError as exc:
            return f"Invalid JSON arguments: {exc.msg}"
        if not isinstance(parsed, dict):
            return "JSON arguments must decode to an object."
        arguments = parsed
    return session.describe_mcp_tool_diagnostic(server_name, tool_name, arguments=arguments)


def _mcp_verify(session: "Session", args: str) -> str:
    raw = args.strip()
    if not raw:
        return "Usage: /mcp-verify <server> <tool> [json-args]"
    parts = raw.split(maxsplit=2)
    if len(parts) < 2:
        return "Usage: /mcp-verify <server> <tool> [json-args]"
    server_name, tool_name = parts[0], parts[1]
    arguments: dict | None = None
    if len(parts) == 3:
        try:
            parsed = json.loads(parts[2])
        except json.JSONDecodeError as exc:
            return f"Invalid JSON arguments: {exc.msg}"
        if not isinstance(parsed, dict):
            return "JSON arguments must decode to an object."
        arguments = parsed
    return session.describe_mcp_verification(server_name, tool_name, arguments=arguments)


def _plugin_command(session: "Session", args: str) -> str:
    raw = args.strip()
    if not raw or raw == "list":
        return session.describe_plugins()
    parts = raw.split(maxsplit=1)
    action = parts[0]
    remainder = parts[1] if len(parts) > 1 else ""
    if action == "show":
        if not remainder:
            return "Usage: /plugin show <plugin-name>"
        return session.describe_plugin(remainder)
    if action == "enable":
        if not remainder:
            return "Usage: /plugin enable <plugin-name>"
        return session.enable_plugin(remainder)
    if action == "disable":
        if not remainder:
            return "Usage: /plugin disable <plugin-name>"
        return session.disable_plugin(remainder)
    return session.describe_plugin(raw)


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..session import Session
