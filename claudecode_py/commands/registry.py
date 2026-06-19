from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(slots=True, frozen=True)
class CommandExecution:
    prompt: str
    allowed_tool_names: tuple[str, ...] | None = None
    allowed_bash_command_prefixes: tuple[str, ...] | None = None
    require_read_only_subagents: bool = False
    progress_message: str = "Running command"
    metadata: dict[str, object] | None = None


CommandHandler = Callable[["Session", str], str | CommandExecution | None]


@dataclass(slots=True, frozen=True)
class ReplCommand:
    name: str
    description: str
    handler: CommandHandler


class CommandRegistry:
    def __init__(self, commands: list[ReplCommand] | None = None) -> None:
        self._commands: dict[str, ReplCommand] = {}
        for command in commands or []:
            self.add_command(command)

    def add_command(self, command: ReplCommand) -> None:
        self._commands[command.name] = command

    def has_command(self, name: str) -> bool:
        return name in self._commands

    def render_help(self) -> str:
        lines = ["Available REPL commands:"]
        width = max((len(name) for name in self._commands), default=0)
        for name in sorted(self._commands):
            command = self._commands[name]
            lines.append(f"{name.ljust(width)}  {command.description}")
        return "\n".join(lines)

    def handle(self, session: "Session", prompt: str) -> tuple[bool, str | CommandExecution | None]:
        raw = prompt.strip()
        if not raw:
            return False, None
        if " " in raw:
            command_name, args = raw.split(" ", 1)
            args = args.strip()
        else:
            command_name, args = raw, ""
        command = self._commands.get(command_name)
        if command is None:
            return False, None
        return True, command.handler(session, args)


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..session import Session
