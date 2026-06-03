from __future__ import annotations

from .registry import AgentDefinition, AgentDefinitionRegistry


def build_builtin_agent_registry() -> AgentDefinitionRegistry:
    registry = AgentDefinitionRegistry()
    registry.set_source_root("builtin", None)
    for definition in (
        AgentDefinition(
            name="default",
            description="Primary interactive session.",
            execution="foreground",
            notes="primary interactive session",
        ),
        AgentDefinition(
            name="background",
            description="Workspace background execution session.",
            execution="background-session",
            notes="workspace background execution",
        ),
        AgentDefinition(
            name="isolated_workspace",
            description="Background execution on an isolated workspace snapshot.",
            execution="background-session+snapshot",
            notes="workspace cloned before execution",
        ),
        AgentDefinition(
            name="read_only_planning",
            description="Planning-only child session with writes blocked.",
            execution="child-session",
            tool_policy="read-only-subagent",
            notes="writes blocked for planning-only delegation",
        ),
    ):
        registry.add_definition(definition)
    return registry
