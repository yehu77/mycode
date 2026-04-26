from .builtin import build_default_command_registry
from .registry import CommandExecution, CommandRegistry, ReplCommand


def render_repl_command_help(registry: CommandRegistry) -> str:
    return (
        registry.render_help()
        + "\n/context-refresh  Reload project memory and skills from disk"
        + "\n/exit             Exit the REPL"
    )


__all__ = [
    "build_default_command_registry",
    "render_repl_command_help",
    "CommandExecution",
    "CommandRegistry",
    "ReplCommand",
]
