from __future__ import annotations

from dataclasses import dataclass

from .registry import AgentDefinition, AgentDefinitionRegistry

BUILTIN_DEFAULT_AGENT_NAME = "default"
BUILTIN_BACKGROUND_AGENT_NAME = "background"
BUILTIN_ISOLATED_WORKSPACE_AGENT_NAME = "isolated_workspace"
BUILTIN_READ_ONLY_PLANNING_AGENT_NAME = "read_only_planning"
BUILTIN_EXPLORE_AGENT_NAME = "Explore"
BUILTIN_PLAN_AGENT_NAME = "Plan"
BUILTIN_PLANNING_AGENT_NAMES = (
    BUILTIN_EXPLORE_AGENT_NAME,
    BUILTIN_PLAN_AGENT_NAME,
)
BUILTIN_PLANNING_AGENT_EXECUTION = "child-session"
BUILTIN_PLANNING_AGENT_TOOL_POLICY = "read-only-subagent"


@dataclass(slots=True, frozen=True)
class BuiltinPlanningAgentContract:
    name: str
    workflow_phase: str
    contribution_kind: str
    role_summary: str
    output_expectations: tuple[str, ...]
    main_thread_usage: str
    misuse_guardrails: tuple[str, ...]


def builtin_planning_agent_names() -> tuple[str, ...]:
    return BUILTIN_PLANNING_AGENT_NAMES


def is_builtin_planning_agent_name(name: str) -> bool:
    return name in BUILTIN_PLANNING_AGENT_NAMES


def get_builtin_planning_agent_contract(
    name: str | None,
) -> BuiltinPlanningAgentContract | None:
    if name == BUILTIN_EXPLORE_AGENT_NAME:
        return BuiltinPlanningAgentContract(
            name=BUILTIN_EXPLORE_AGENT_NAME,
            workflow_phase="phase_1_initial_understanding",
            contribution_kind="reconnaissance_findings",
            role_summary=(
                "Phase 1 reconnaissance agent for codebase search, reuse discovery, "
                "and code path tracing before design."
            ),
            output_expectations=(
                "Relevant files and modules",
                "Existing implementations and reuse candidates",
                "Code path traces and runtime/data-flow notes",
                "Risks, constraints, and remaining unknowns",
            ),
            main_thread_usage=(
                "Use as Phase 1 reconnaissance input for main-thread review and Phase 2 design."
            ),
            misuse_guardrails=(
                "Do not act like a final implementation-planning agent.",
                "Do not replace Phase 2 design work with a polished implementation proposal unless explicitly asked.",
            ),
        )
    if name == BUILTIN_PLAN_AGENT_NAME:
        return BuiltinPlanningAgentContract(
            name=BUILTIN_PLAN_AGENT_NAME,
            workflow_phase="phase_2_design",
            contribution_kind="implementation_design",
            role_summary=(
                "Phase 2 design agent for synthesizing exploration findings into an "
                "implementation approach and execution plan."
            ),
            output_expectations=(
                "Ordered implementation steps",
                "Critical files to change",
                "Reuse notes from exploration",
                "Verification ideas and remaining design risks",
            ),
            main_thread_usage=(
                "Use as Phase 2 design input for main-thread review, final plan writing, and user clarification."
            ),
            misuse_guardrails=(
                "Do not behave like another reconnaissance or search-only agent.",
                "Do not skip implementation structure, file impact, or verification planning.",
            ),
        )
    return None


def build_builtin_planning_agent_prompt(
    name: str | None,
    *,
    description: str,
    prompt: str,
) -> str | None:
    contract = get_builtin_planning_agent_contract(name)
    if contract is None:
        return None
    if name == BUILTIN_EXPLORE_AGENT_NAME:
        return (
            "You are the builtin Explore planning agent.\n"
            "Your role is reconnaissance before design. Stay read-only.\n"
            "Do not modify files, do not create commits, and do not run commands with side effects.\n"
            "Use only inspection tools and read-only shell commands.\n"
            "Focus on:\n"
            "- locating relevant files and modules\n"
            "- identifying existing implementations and reuse candidates\n"
            "- tracing key code paths, runtime flow, or data flow\n"
            "- surfacing risks, constraints, and remaining unknowns\n"
            "Do not produce a final implementation plan unless the parent explicitly asks for that. "
            "Your job is to reduce uncertainty and support Phase 2 design work.\n\n"
            "Return markdown with these sections:\n"
            "1. Relevant Files\n"
            "2. Existing Implementations And Reuse Candidates\n"
            "3. Code Path Traces\n"
            "4. Risks And Unknowns\n\n"
            f"Subtask: {description}\n\n"
            f"{prompt}"
        )
    if name == BUILTIN_PLAN_AGENT_NAME:
        return (
            "You are the builtin Plan planning agent.\n"
            "Your role is Phase 2 implementation design after exploration. Stay read-only.\n"
            "Do not modify files, do not create commits, and do not run commands with side effects.\n"
            "Use the exploration context you were given to produce an implementation approach, not another search pass.\n"
            "Focus on:\n"
            "- synthesizing Phase 1 findings into an implementation approach\n"
            "- producing ordered implementation steps\n"
            "- identifying the critical files to change\n"
            "- calling out existing code that should be reused\n"
            "- suggesting verification ideas and noting remaining design risks or tradeoffs\n"
            "Do not fall back to generic reconnaissance unless the parent explicitly asks for more exploration. "
            "Your job is to support final plan synthesis for the main thread.\n\n"
            "Return markdown with these sections:\n"
            "1. Implementation Approach\n"
            "2. Ordered Steps\n"
            "3. Critical Files To Change\n"
            "4. Reuse Notes From Exploration\n"
            "5. Verification Ideas\n"
            "6. Risks And Tradeoffs\n\n"
            f"Subtask: {description}\n\n"
            f"{prompt}"
        )
    return None


def get_builtin_planning_agent_definitions(
    registry: AgentDefinitionRegistry | None = None,
) -> tuple[AgentDefinition, ...]:
    resolved = registry or build_builtin_agent_registry()
    definitions: list[AgentDefinition] = []
    for name in BUILTIN_PLANNING_AGENT_NAMES:
        definition = resolved.get_definition(name)
        if definition is None:
            raise ValueError(f'Missing builtin planning agent definition "{name}".')
        definitions.append(definition)
    return tuple(definitions)


def validate_builtin_planning_agent_registry(
    registry: AgentDefinitionRegistry,
) -> tuple[AgentDefinition, ...]:
    definitions = get_builtin_planning_agent_definitions(registry)
    base_definition = registry.get_definition(BUILTIN_READ_ONLY_PLANNING_AGENT_NAME)
    for definition in definitions:
        if definition.source != "builtin":
            raise ValueError(
                f'Builtin planning agent "{definition.name}" must come from source=builtin, '
                f"got source={definition.source}."
            )
        if definition.based_on != BUILTIN_READ_ONLY_PLANNING_AGENT_NAME:
            raise ValueError(
                f'Builtin planning agent "{definition.name}" must inherit from '
                f'"{BUILTIN_READ_ONLY_PLANNING_AGENT_NAME}".'
            )
        effective_execution = (
            definition.execution
            or (base_definition.execution if base_definition is not None else None)
            or "foreground"
        )
        effective_tool_policy = (
            definition.tool_policy
            or (base_definition.tool_policy if base_definition is not None else None)
        )
        if effective_execution != BUILTIN_PLANNING_AGENT_EXECUTION:
            raise ValueError(
                f'Builtin planning agent "{definition.name}" must resolve to '
                f'execution="{BUILTIN_PLANNING_AGENT_EXECUTION}", got "{effective_execution}".'
            )
        if effective_tool_policy != BUILTIN_PLANNING_AGENT_TOOL_POLICY:
            raise ValueError(
                f'Builtin planning agent "{definition.name}" must resolve to '
                f'tool_policy="{BUILTIN_PLANNING_AGENT_TOOL_POLICY}", got "{effective_tool_policy}".'
            )
    return definitions


def build_builtin_agent_registry() -> AgentDefinitionRegistry:
    registry = AgentDefinitionRegistry()
    registry.set_source_root("builtin", None)
    for definition in (
        AgentDefinition(
            name=BUILTIN_DEFAULT_AGENT_NAME,
            description="Primary interactive session.",
            execution="foreground",
            notes="primary interactive session",
        ),
        AgentDefinition(
            name=BUILTIN_BACKGROUND_AGENT_NAME,
            description="Workspace background execution session.",
            execution="background-session",
            notes="workspace background execution",
        ),
        AgentDefinition(
            name=BUILTIN_ISOLATED_WORKSPACE_AGENT_NAME,
            description="Background execution on an isolated workspace snapshot.",
            execution="background-session+snapshot",
            notes="workspace cloned before execution",
        ),
        AgentDefinition(
            name=BUILTIN_READ_ONLY_PLANNING_AGENT_NAME,
            description="Planning-only child session with writes blocked.",
            execution="child-session",
            tool_policy="read-only-subagent",
            notes="writes blocked for planning-only delegation",
        ),
        AgentDefinition(
            name=BUILTIN_EXPLORE_AGENT_NAME,
            description="Read-only Phase 1 reconnaissance agent for codebase search, reuse discovery, and code path tracing.",
            based_on=BUILTIN_READ_ONLY_PLANNING_AGENT_NAME,
            notes="plan-mode phase 1 reconnaissance agent; supports discovery and reuse mapping, not final design",
        ),
        AgentDefinition(
            name=BUILTIN_PLAN_AGENT_NAME,
            description="Read-only Phase 2 design agent for implementation planning, critical-file mapping, and verification shaping.",
            based_on=BUILTIN_READ_ONLY_PLANNING_AGENT_NAME,
            notes="plan-mode phase 2 design agent; synthesizes exploration into ordered implementation and verification guidance",
        ),
    ):
        registry.add_definition(definition)
    validate_builtin_planning_agent_registry(registry)
    return registry
