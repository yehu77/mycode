from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..commands import CommandRegistry
from ..commands.builtin import build_core_command_registry, build_core_commands
from ..config import SessionConfig
from ..indexing import (
    JsTsProjectIndex,
    PythonProjectIndex,
    build_js_ts_project_index,
    build_python_project_index,
    snapshot_js_ts_tree,
    snapshot_python_tree,
)
from ..integrations import SymbolLocation
from ..mcp import McpRegistry
from ..permissions import PermissionManager
from ..plugins import PluginRegistry, build_builtin_plugin_registry
from ..prompts import SYSTEM_PROMPT_TEMPLATE, compose_system_prompt
from ..providers import build_provider
from ..skills import LoadedSkill, ProjectContext, load_project_context
from ..state import SessionState
from ..tasks import TaskManager
from ..tools import (
    AgentTool,
    ApplyPatchTool,
    BaseTool,
    BashTool,
    EditFileTool,
    FindCalleesTool,
    FindCallersTool,
    FindReferencesTool,
    FindSymbolGraphTool,
    FindSymbolTool,
    GlobTool,
    GrepTool,
    ListDirTool,
    McpToolAdapter,
    OutlineFileTool,
    OutlineProjectTool,
    ReadFileTool,
    TaskGetTool,
    TaskListTool,
    TaskStopTool,
    TaskWaitTool,
    ToolContext,
    WriteFileTool,
)
from .orchestrator import ToolOrchestrator

if TYPE_CHECKING:
    from ..session import Session


@dataclass(slots=True)
class SessionRuntimeContext:
    config: SessionConfig
    task_manager: TaskManager
    provider: Any
    plugin_registry: PluginRegistry
    command_registry: CommandRegistry
    mcp_registry: McpRegistry | None
    project_context: ProjectContext
    base_system_prompt: str
    tools: list[BaseTool]
    orchestrator: ToolOrchestrator
    owns_mcp_registry: bool = True
    _python_symbol_index: PythonProjectIndex | None = None
    _js_ts_symbol_index: JsTsProjectIndex | None = None

    def tool_context(
        self,
        *,
        session: Session,
        permission_manager: PermissionManager,
    ) -> ToolContext:
        return ToolContext(
            cwd=self.config.cwd,
            permission_manager=permission_manager,
            task_manager=self.task_manager,
            session=session,
        )

    def build_system_prompt(self, state: SessionState) -> str:
        auto_enabled_skills, manually_enabled_skills = self.active_skills_by_source(state)
        planning_context = None
        latest = _active_planning_artifact(state)
        if latest is not None:
            planning_context = (
                f"artifact_id: {latest.artifact_id}\n"
                f"kind: {latest.kind}\n"
                f"goal: {latest.goal}\n"
                f"summary:\n{latest.summary}"
            )
        return compose_system_prompt(
            base_prompt=self.base_system_prompt,
            project_context=self.project_context,
            auto_enabled_skills=auto_enabled_skills,
            manually_enabled_skills=manually_enabled_skills,
            context_summary=state.context_summary,
            planning_context=planning_context,
        )

    def active_skills(self, state: SessionState) -> list[LoadedSkill]:
        auto_enabled_skills, manually_enabled_skills = self.active_skills_by_source(state)
        return [*auto_enabled_skills, *manually_enabled_skills]

    def active_skills_by_source(self, state: SessionState) -> tuple[list[LoadedSkill], list[LoadedSkill]]:
        auto_enabled: list[LoadedSkill] = []
        manually_enabled: list[LoadedSkill] = []
        manual_enabled_names = set(state.enabled_skill_names)
        manual_disabled_names = set(state.disabled_skill_names)
        for skill in self.project_context.skills:
            if skill.name in manual_disabled_names:
                continue
            if skill.auto_enable and skill.name not in manual_enabled_names:
                auto_enabled.append(skill)
                continue
            if skill.name in manual_enabled_names:
                manually_enabled.append(skill)
        return auto_enabled, manually_enabled

    def reload_project_context(self, state: SessionState) -> None:
        base_context = load_project_context(self.config.cwd)
        self.project_context = self.plugin_registry.build_project_context(
            base_context,
            state,
            cwd=self.config.cwd,
        )

    def refresh_command_registry(self, state: SessionState) -> None:
        self.command_registry = self.plugin_registry.build_command_registry(
            state,
            base_commands=build_core_commands(),
        )

    def describe_plugins(self, state: SessionState) -> str:
        return self.plugin_registry.describe_plugins(state)

    def replace_mcp_registry(
        self,
        mcp_registry: McpRegistry | None,
        *,
        owns_registry: bool | None = None,
    ) -> None:
        previous = self.mcp_registry
        previous_owned = self.owns_mcp_registry
        if owns_registry is None:
            owns_registry = previous_owned if previous is mcp_registry else True
        self.mcp_registry = mcp_registry
        self.owns_mcp_registry = owns_registry
        self.tools = _build_tools(mcp_registry)
        self.orchestrator = ToolOrchestrator(self.tools)
        if previous is not None and previous is not mcp_registry and previous_owned:
            previous.close()

    def get_python_symbol_index(self) -> PythonProjectIndex:
        current_signature = snapshot_python_tree(self.config.cwd)
        cached = self._python_symbol_index
        if cached is not None and cached.signature == current_signature:
            return cached
        self._python_symbol_index = build_python_project_index(self.config.cwd)
        return self._python_symbol_index

    def get_js_ts_symbol_index(self) -> JsTsProjectIndex:
        current_signature = snapshot_js_ts_tree(self.config.cwd)
        cached = self._js_ts_symbol_index
        if cached is not None and cached.signature == current_signature:
            return cached
        self._js_ts_symbol_index = build_js_ts_project_index(self.config.cwd)
        return self._js_ts_symbol_index

    def locate_symbols(
        self,
        symbol: str,
        *,
        base: Path,
        max_results: int = 50,
    ) -> tuple[SymbolLocation, ...]:
        path_filter = None if base == self.config.cwd else base.relative_to(self.config.cwd).as_posix()
        matches: list[SymbolLocation] = []
        for index in (self.get_python_symbol_index(), self.get_js_ts_symbol_index()):
            remaining = max_results - len(matches)
            if remaining <= 0:
                break
            for entry in index.find(symbol, path_filter=path_filter, max_results=remaining):
                matches.append(
                    SymbolLocation(
                        symbol=entry.name,
                        kind=entry.kind,
                        path=entry.rel_path,
                        line=entry.line,
                        owner=entry.owner,
                    )
                )
        return tuple(matches)

    def close(self) -> None:
        if self.mcp_registry is None or not self.owns_mcp_registry:
            return
        self.mcp_registry.close()


def build_session_runtime_context(
    config: SessionConfig,
    *,
    initial_state: SessionState | None = None,
    task_manager: TaskManager | None = None,
    mcp_registry: McpRegistry | None = None,
    owns_mcp_registry: bool = True,
    provider: Any | None = None,
    command_registry: CommandRegistry | None = None,
    plugin_registry: PluginRegistry | None = None,
) -> SessionRuntimeContext:
    resolved_state = initial_state or SessionState()
    resolved_task_manager = task_manager or TaskManager()
    resolved_provider = provider or build_provider(
        provider=config.provider,
        model=config.model,
        max_tokens=config.max_tokens,
        api_key=config.api_key,
        base_url=config.base_url,
    )
    resolved_plugin_registry = plugin_registry or build_builtin_plugin_registry()
    resolved_command_registry = command_registry or resolved_plugin_registry.build_command_registry(
        resolved_state,
        base_commands=build_core_commands(),
    )
    tools = _build_tools(mcp_registry)
    base_project_context = load_project_context(config.cwd)
    return SessionRuntimeContext(
        config=config,
        task_manager=resolved_task_manager,
        provider=resolved_provider,
        plugin_registry=resolved_plugin_registry,
        command_registry=resolved_command_registry,
        mcp_registry=mcp_registry,
        project_context=resolved_plugin_registry.build_project_context(
            base_project_context,
            resolved_state,
            cwd=config.cwd,
        ),
        base_system_prompt=SYSTEM_PROMPT_TEMPLATE.format(
            cwd=str(config.cwd),
            workspace_name=config.cwd.name,
        ),
        tools=tools,
        orchestrator=ToolOrchestrator(tools),
        owns_mcp_registry=owns_mcp_registry,
    )
def _build_tools(mcp_registry: McpRegistry | None) -> list[BaseTool]:
    tools: list[BaseTool] = [
        ListDirTool(),
        ReadFileTool(),
        OutlineFileTool(),
        OutlineProjectTool(),
        FindSymbolTool(),
        FindSymbolGraphTool(),
        FindCallersTool(),
        FindCalleesTool(),
        FindReferencesTool(),
        GlobTool(),
        GrepTool(),
        WriteFileTool(),
        EditFileTool(),
        ApplyPatchTool(),
        BashTool(),
        AgentTool(),
        TaskListTool(),
        TaskGetTool(),
        TaskStopTool(),
        TaskWaitTool(),
    ]
    if mcp_registry is not None:
        for reference in mcp_registry.list_tool_references():
            server = mcp_registry.get_server(reference.server_name)
            tools.append(McpToolAdapter(client=server.client, reference=reference))
    return tools


def _active_planning_artifact(state: SessionState):
    if state.active_planning_artifact_id:
        for artifact in state.planning_artifact_history or state.recent_planning_artifacts:
            if artifact.artifact_id == state.active_planning_artifact_id:
                return artifact
    artifacts = state.planning_artifact_history or state.recent_planning_artifacts
    if artifacts:
        return artifacts[-1]
    return None
