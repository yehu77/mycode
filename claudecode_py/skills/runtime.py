from __future__ import annotations

from pathlib import Path
from shlex import split as shlex_split
from typing import TYPE_CHECKING

from ..commands import CommandExecution, ReplCommand
from .loader import LoadedSkill

if TYPE_CHECKING:
    from ..session import Session


def is_model_invocable_skill(skill: LoadedSkill) -> bool:
    return bool(skill.user_invocable and not skill.disable_model_invocation)


def find_user_invocable_skill(skills: list[LoadedSkill], skill_name: str) -> LoadedSkill | None:
    normalized = skill_name.strip()
    if normalized.startswith("/"):
        normalized = normalized[1:]
    for skill in skills:
        if skill.user_invocable and skill.name == normalized:
            return skill
    return None


def build_skill_command_execution(skill: LoadedSkill, raw_args: str) -> CommandExecution:
    prompt = expand_skill_prompt(skill, raw_args)
    execution_context = (skill.execution_context or "inline").strip() or "inline"
    return CommandExecution(
        prompt=prompt,
        allowed_tool_names=skill.allowed_tool_names or None,
        allowed_bash_command_prefixes=skill.allowed_bash_command_prefixes or None,
        progress_message=(
            f"Running forked skill /{skill.name}"
            if execution_context == "fork"
            else f"Running skill /{skill.name}"
        ),
        metadata={
            "command_kind": "skill-fork" if execution_context == "fork" else "skill",
            "command_policy_name": f"skill:{skill.name}",
            "command_policy_source": f"skill:/{skill.name}",
            "skill_name": skill.name,
            "skill_execution_context": execution_context,
            "skill_model_override": skill.model,
            "skill_effort_override": skill.effort,
        },
    )


def resolve_skill_command_execution(
    skills: list[LoadedSkill],
    skill_name: str,
    raw_args: str,
) -> CommandExecution | str:
    skill = find_user_invocable_skill(skills, skill_name)
    if skill is None:
        return f'Unknown user-invocable skill "{skill_name}".'
    return build_skill_command_execution(skill, raw_args)


def build_user_invocable_skill_commands(skills: list[LoadedSkill]) -> list[ReplCommand]:
    commands: list[ReplCommand] = []
    for skill in skills:
        if not skill.user_invocable:
            continue
        description = skill.description or skill.when_to_use or f"Run skill /{skill.name}"

        def _handler(session: Session, args: str, *, skill_name: str = skill.name):
            return resolve_skill_command_execution(session.project_context.skills, skill_name, args)

        commands.append(
            ReplCommand(
                name=f"/{skill.name}",
                description=description,
                handler=_handler,
            )
        )
    return commands


def expand_skill_prompt(skill: LoadedSkill, raw_args: str) -> str:
    bindings = _skill_bindings(skill, raw_args)
    expanded = skill.content
    for name in sorted(bindings, key=len, reverse=True):
        value = bindings[name]
        expanded = expanded.replace(f"${{{name}}}", value)
        expanded = expanded.replace(f"${name}", value)
    return expanded


def _skill_bindings(skill: LoadedSkill, raw_args: str) -> dict[str, str]:
    bindings = {
        "ARGS": raw_args,
        "args": raw_args,
        "CLAUDE_SKILL_DIR": str(skill.skill_root or Path(skill.path).parent),
    }
    argument_names = list(skill.arguments)
    if not argument_names:
        return bindings
    if len(argument_names) == 1:
        bindings[argument_names[0]] = raw_args
        return bindings
    tokens = _shell_like_split(raw_args)
    for index, argument_name in enumerate(argument_names):
        if index == len(argument_names) - 1:
            bindings[argument_name] = " ".join(tokens[index:]).strip()
            break
        bindings[argument_name] = tokens[index] if index < len(tokens) else ""
    return bindings


def _shell_like_split(raw_args: str) -> list[str]:
    if not raw_args.strip():
        return []
    try:
        return shlex_split(raw_args, posix=True)
    except ValueError:
        return raw_args.split()
