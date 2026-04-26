from __future__ import annotations

import json

from .local_commands import handle_plan_command

from .registry import CommandRegistry, ReplCommand


def build_core_commands() -> list[ReplCommand]:
    return [
        ReplCommand("/help", "Show this help text", lambda session, args: ""),
        ReplCommand("/tools", "List available tools", lambda session, args: session.describe_tools()),
        ReplCommand(
            "/model",
            "Show provider/model capability summary",
            lambda session, args: session.describe_provider(),
        ),
        ReplCommand(
            "/config",
            "Show current session configuration",
            lambda session, args: session.describe_config(),
        ),
        ReplCommand(
            "/history",
            "Show recent conversation history",
            lambda session, args: session.describe_history(),
        ),
        ReplCommand(
            "/changes",
            "Show recent workspace changes recorded for undo",
            lambda session, args: session.describe_recent_changes(),
        ),
        ReplCommand(
            "/sessions",
            "Show saved sessions in the workspace",
            lambda session, args: session.describe_saved_sessions(),
        ),
        ReplCommand(
            "/tasks",
            "Show background tasks",
            lambda session, args: session.describe_tasks(),
        ),
        ReplCommand(
            "/plan",
            "Inspect or manage planning artifacts",
            lambda session, args: handle_plan_command(session, args),
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
            "Clear in-memory conversation history for this session",
            _clear_history,
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


def _clear_history(session: "Session", _args: str) -> str:
    session.clear_history()
    return "Cleared in-memory conversation history for this session."


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
