from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import Thread
from typing import Any
import difflib
import re

from .commands import CommandExecution, render_repl_command_help
from .config import SessionConfig
from .indexing import (
    JsTsProjectIndex,
    PythonProjectIndex,
)
from .integrations import (
    DiffTargetResult,
    EditorTarget,
    ReferenceLookupResult,
    ReferenceTargetResult,
    SymbolActionBundle,
    SymbolLookupResult,
    build_diff_targets,
    build_open_file_target,
    build_reference_targets,
    build_symbol_action_bundle,
    build_symbol_target,
    parse_reference_line,
)
from .mcp import McpRegistry
from .mcp.client import McpClientError
from .mcp.models import McpDiagnosticResult, McpVerificationResult
from .permissions import PermissionManager
from .providers import build_provider, format_capabilities
from .providers.errors import ProviderCapabilityError
from .runtime.events import RuntimeEvent
from .runtime.context import SessionRuntimeContext
from .runtime.query_loop import _create_provider_message_with_retries, _request_advisor_review, run_query_loop
from .session_factory import SessionFactory, _UNSET
from .storage.transcript import list_transcripts, save_transcript
from .state import (
    AdvisorReviewSummary,
    PlanningArtifact,
    SessionState,
    WorkspaceChangeSet,
    WorkspaceFileChange,
)
from .tasks import TaskManager
from .tools import FindReferencesTool, ToolContext
from .tools.base import render_change_detail, render_change_summary, resolve_workspace_path
from .tools.mcp import make_mcp_tool_name


READ_ONLY_SUBAGENT_TOOL_NAMES = (
    "bash",
    "list_dir",
    "read_file",
    "outline_file",
    "outline_project",
    "glob",
    "grep",
    "find_symbol",
    "find_references",
    "find_symbol_graph",
    "find_callers",
    "find_callees",
    "task_list",
    "task_get",
    "task_wait",
)

READ_ONLY_SUBAGENT_BASH_PREFIXES = (
    "pwd",
    "git status",
    "git diff",
    "git log",
    "git show",
    "git branch",
    "git ls-files",
    "git grep",
    "rg ",
    "grep ",
    "ls",
    "dir",
)

ADVISOR_MODES = ("off", "final-review", "interactive-review")
WRITE_TOOL_NAMES = frozenset({"write_file", "edit_file", "apply_patch"})
MAX_ADVISOR_HISTORY = 20
MAX_PLANNING_ARTIFACTS = 5


@dataclass(slots=True, frozen=True)
class _UltraplanScoutDefinition:
    category: str
    description: str
    prompt: str


class Session:
    def __init__(
        self,
        config: SessionConfig,
        *,
        state: SessionState | None = None,
        task_manager: TaskManager | None = None,
        mcp_registry: McpRegistry | None | object = _UNSET,
        depth: int = 0,
        persist_transcript: bool | None = None,
        runtime_context: SessionRuntimeContext | None = None,
        session_factory: SessionFactory | None = None,
        permission_manager: PermissionManager | None = None,
    ) -> None:
        self._session_factory = session_factory or SessionFactory(load_mcp_from_config=False)
        self._runtime_context = runtime_context or self._session_factory.create_runtime_context(
            config,
            task_manager=task_manager,
            mcp_registry=mcp_registry,
        )
        self.state = state or SessionState()
        self._workspace_cleanup = None
        self._normalize_advisor_state()
        self._normalize_workspace_state()
        self.persist_transcript = depth == 0 if persist_transcript is None else persist_transcript
        self.permission_manager = permission_manager or PermissionManager(
            mode=config.permission_mode,
            interactive=config.interactive,
        )
        self.depth = depth
        self._active_tool_names: frozenset[str] | None = None
        self._active_bash_command_prefixes: tuple[str, ...] | None = None
        self._require_read_only_subagents = False
        self._turn_read_only_constraints_active = False
        self._reconcile_plugin_state(initial_load=True)
        self._reconcile_skill_state(initial_load=True)
        self._refresh_command_registry()

    @property
    def config(self) -> SessionConfig:
        return self._runtime_context.config

    @property
    def task_manager(self) -> TaskManager:
        return self._runtime_context.task_manager

    @property
    def command_registry(self):
        return self._runtime_context.command_registry

    @property
    def plugin_registry(self):
        return self._runtime_context.plugin_registry

    @property
    def mcp_registry(self) -> McpRegistry | None:
        return self._runtime_context.mcp_registry

    @property
    def provider(self):
        return self._runtime_context.provider

    @provider.setter
    def provider(self, value) -> None:
        self._runtime_context.provider = value

    @property
    def project_context(self):
        return self._runtime_context.project_context

    @property
    def base_system_prompt(self) -> str:
        return self._runtime_context.base_system_prompt

    @property
    def tools(self):
        return self._runtime_context.tools

    @property
    def orchestrator(self):
        return self._runtime_context.orchestrator

    def tool_specs(self) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        for tool in self._available_tools():
            spec = tool.to_model_tool()
            if tool.name == "bash" and self._active_bash_command_prefixes:
                allowed = ", ".join(self._active_bash_command_prefixes)
                spec = {
                    **spec,
                    "description": f'{spec["description"]} Allowed commands in this turn must start with: {allowed}.',
                }
            specs.append(spec)
        return specs

    def execute_tool_calls(self, tool_calls, ctx: ToolContext, *, sink=None) -> list[dict[str, Any]]:
        return self._build_active_orchestrator().execute_tool_calls(tool_calls, ctx, sink=sink)

    def tool_context(self) -> ToolContext:
        return self._runtime_context.tool_context(
            session=self,
            permission_manager=self.permission_manager,
        )

    def ask(
        self,
        prompt: str,
        sink=None,
        *,
        allowed_tool_names: tuple[str, ...] | None = None,
        allowed_bash_command_prefixes: tuple[str, ...] | None = None,
        require_read_only_subagents: bool = False,
    ) -> str:
        with self._command_execution_scope(
            allowed_tool_names=allowed_tool_names,
            allowed_bash_command_prefixes=allowed_bash_command_prefixes,
            require_read_only_subagents=require_read_only_subagents,
        ):
            result = run_query_loop(self, prompt, sink=sink)
        self.persist_state()
        return result

    def run_command(self, execution: CommandExecution, sink=None) -> str:
        metadata = execution.metadata or {}
        if metadata.get("command_kind") == "ultraplan":
            goal = str(metadata.get("goal") or execution.prompt).strip()
            scout_categories = tuple(str(item) for item in metadata.get("scout_categories", []))
            supersede_artifact_id = metadata.get("supersede_artifact_id")
            derived_from_drift = bool(metadata.get("derived_from_drift", False))
            derivation_reason = metadata.get("derivation_reason")
            return self.run_ultraplan(
                goal=goal,
                scout_categories=scout_categories,
                supersede_artifact_id=(
                    str(supersede_artifact_id)
                    if supersede_artifact_id is not None
                    else None
                ),
                derived_from_drift=derived_from_drift,
                derivation_reason=(
                    str(derivation_reason)
                    if derivation_reason is not None
                    else None
                ),
                sink=sink,
            )
        return self.ask(
            execution.prompt,
            sink=sink,
            allowed_tool_names=execution.allowed_tool_names,
            allowed_bash_command_prefixes=execution.allowed_bash_command_prefixes,
            require_read_only_subagents=execution.require_read_only_subagents,
        )

    def handle_repl_command(self, prompt: str) -> tuple[bool, str | CommandExecution | None]:
        if prompt == "/help":
            return True, render_repl_command_help(self.command_registry)
        if prompt == "/context-refresh":
            return True, self.reload_project_context()
        return self.command_registry.handle(self, prompt)

    def persist_state(self) -> None:
        if self.persist_transcript:
            save_transcript(self.config, self.state)

    def set_workspace_cleanup(self, callback) -> None:
        self._workspace_cleanup = callback

    def build_system_prompt(self) -> str:
        return self._runtime_context.build_system_prompt(self.state)

    def latest_planning_artifact(self) -> PlanningArtifact | None:
        artifacts = self.planning_artifacts()
        if not artifacts:
            return None
        active = self.active_planning_artifact()
        if active is not None:
            return active
        return artifacts[-1]

    def planning_artifacts(self) -> list[PlanningArtifact]:
        artifacts = (
            self.state.planning_artifact_history
            if self.state.planning_artifact_history
            else self.state.recent_planning_artifacts
        )
        return list(artifacts)

    def active_planning_artifact(self) -> PlanningArtifact | None:
        artifacts = self.planning_artifacts()
        active_id = self.state.active_planning_artifact_id
        if active_id:
            for artifact in artifacts:
                if artifact.artifact_id == active_id:
                    return artifact
        return None

    def resolve_planning_artifact(self, identifier: str) -> PlanningArtifact | None:
        artifacts = self.planning_artifacts()
        raw = identifier.strip()
        if not raw:
            return None
        if raw == "latest":
            return artifacts[-1] if artifacts else None
        matches = [
            artifact
            for artifact in artifacts
            if artifact.artifact_id == raw
            or artifact.artifact_id.startswith(raw)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def advisor_block_count(self) -> int:
        return sum(1 for item in self.state.advisor_review_history if item.status == "block")

    def turn_read_only_constraints_active(self) -> bool:
        return self._turn_read_only_constraints_active

    def set_turn_read_only_constraints_active(self, value: bool) -> None:
        self._turn_read_only_constraints_active = value
        if value:
            self.state.active_execution_constraint = "read-only"
        elif self.state.active_execution_constraint == "read-only":
            self.state.active_execution_constraint = "normal"

    def activate_execution_constraint(
        self,
        *,
        mode: str,
        source: str | None,
        reason: str | None,
        increment: bool = False,
    ) -> None:
        self.state.active_execution_constraint = mode
        self.state.constraint_source = source
        self.state.constraint_reason = reason
        self._turn_read_only_constraints_active = mode == "read-only"
        if increment:
            self.state.constraint_trigger_count += 1

    def clear_execution_constraint(self) -> None:
        self.activate_execution_constraint(mode="normal", source=None, reason=None)

    def begin_plan_execution(self, artifact: PlanningArtifact) -> None:
        self.state.active_execution_plan_id = artifact.artifact_id
        self.state.plan_execution_count += 1

    def clear_plan_execution(self) -> None:
        self.state.active_execution_plan_id = None

    def describe_planning_lifecycle(self) -> list[str]:
        artifact = self.active_planning_artifact()
        lines = [
            f"advisor_blocks: {self.advisor_block_count()}",
            f"execution_constraints: {self.state.active_execution_constraint}",
            f"constraint_triggers: {self.state.constraint_trigger_count}",
            f"plan_executions: {self.state.plan_execution_count}",
            f"plan_drifts: {self.state.plan_drift_count}",
        ]
        if self.state.constraint_source:
            lines.append(f"constraint_source: {self.state.constraint_source}")
        if self.state.constraint_reason:
            lines.append(f"constraint_reason: {self.state.constraint_reason}")
        if self.state.active_execution_plan_id:
            lines.append(f"active_execution_plan_id: {self.state.active_execution_plan_id}")
        if self.state.last_plan_drift_status:
            lines.append(f"last_plan_drift_status: {self.state.last_plan_drift_status}")
        if self.state.last_plan_drift_reason:
            lines.append(f"last_plan_drift_reason: {self.state.last_plan_drift_reason}")
        if self.state.last_plan_drift_context:
            summary = self._recent_plan_drift_summary()
            if summary:
                lines.append(f"last_plan_drift_summary: {summary}")
        if artifact is None:
            lines.append("active_plan: none")
            return lines
        lines.append(f"active_plan_id: {artifact.artifact_id}")
        lines.append(f"active_plan_kind: {artifact.kind}")
        lines.append(f"active_plan_goal: {artifact.goal}")
        lines.append(
            "active_plan_read_only_subagents: "
            f"{'yes' if artifact.used_read_only_subagents else 'no'}"
        )
        lines.append(f"active_plan_scouts: {len(artifact.scout_categories)}")
        lines.append(f"active_plan_tasks: {len(artifact.task_ids)}")
        if artifact.advisor_status:
            lines.append(f"active_plan_advisor_status: {artifact.advisor_status}")
        if artifact.advisor_risk_flags:
            lines.append("active_plan_risk_flags: " + ", ".join(artifact.advisor_risk_flags))
        if artifact.supersedes_artifact_id:
            lines.append(f"active_plan_supersedes: {artifact.supersedes_artifact_id}")
        if artifact.superseded_by_artifact_id:
            lines.append(f"active_plan_superseded_by: {artifact.superseded_by_artifact_id}")
        if artifact.derived_from_drift:
            lines.append("active_plan_derived_from_drift: yes")
        if artifact.derivation_reason:
            lines.append(f"active_plan_derivation_reason: {artifact.derivation_reason}")
        return lines

    def build_turn_prompt(self, prompt: str) -> str:
        artifact = self.active_planning_artifact()
        if artifact is None or artifact.kind != "ultraplan":
            return prompt
        summary = artifact.summary.strip()
        if len(summary) > 1200:
            summary = summary[:1197] + "..."
        lines = [
            "Use the recent ultraplan artifact below as explicit execution context unless the user request overrides it.",
            "",
            f"Plan goal: {artifact.goal}",
            f"Plan summary:\n{summary}",
        ]
        if artifact.advisor_status:
            lines.append(f"Plan advisor status: {artifact.advisor_status}")
        if artifact.advisor_risk_flags:
            lines.append("Plan advisor risk flags: " + ", ".join(artifact.advisor_risk_flags))
        lines.extend(
            [
                "",
                "Current user request:",
                prompt,
            ]
        )
        return "\n".join(lines)

    def active_skills(self):
        return self._runtime_context.active_skills(self.state)

    def active_skills_by_source(self):
        return self._runtime_context.active_skills_by_source(self.state)

    def create_child_session(
        self,
        *,
        interactive: bool = False,
        isolated_workspace: bool = False,
    ) -> "Session":
        return self._session_factory.create_child_session(
            self,
            interactive=interactive,
            isolated_workspace=isolated_workspace,
        )

    def requires_read_only_subagents(self) -> bool:
        return self._require_read_only_subagents

    def run_subagent(
        self,
        *,
        description: str,
        prompt: str,
        isolated_workspace: bool = False,
        read_only: bool = False,
    ) -> str:
        if self.depth >= self.config.max_agent_depth:
            raise RuntimeError("Max agent depth reached.")
        child = self.create_child_session(
            interactive=False,
            isolated_workspace=isolated_workspace,
        )
        child_prompt = f"{description}\n\n{prompt}"
        if read_only:
            child_prompt = _build_read_only_subagent_prompt(description=description, prompt=prompt)
        return child.ask(
            child_prompt,
            allowed_tool_names=READ_ONLY_SUBAGENT_TOOL_NAMES if read_only else None,
            allowed_bash_command_prefixes=READ_ONLY_SUBAGENT_BASH_PREFIXES if read_only else None,
            require_read_only_subagents=read_only,
        )

    def launch_background_agent(
        self,
        *,
        description: str,
        prompt: str,
        isolated_workspace: bool = False,
        read_only: bool = False,
    ) -> str:
        if self.depth >= self.config.max_agent_depth:
            raise RuntimeError("Max agent depth reached.")
        active_plan = self.active_planning_artifact()
        task = self.task_manager.create(
            "agent",
            description,
            parent_session_id=self.state.session_id,
            provider=self.config.provider,
            model=self.config.model,
            cwd=str(self.config.cwd),
            isolated_workspace=isolated_workspace,
            read_only=read_only,
            task_role="execution" if active_plan is not None else "background",
            active_plan_id=active_plan.artifact_id if active_plan is not None else None,
            active_plan_goal=active_plan.goal if active_plan is not None else None,
        )
        self.task_manager.set_progress(task.id, "Queued background agent")

        def worker() -> None:
            try:
                child = self.create_child_session(
                    interactive=False,
                    isolated_workspace=isolated_workspace,
                )
                self.task_manager.set_progress(
                    task.id,
                    "Running background agent",
                    child_cwd=str(child.config.cwd),
                )
                output = child.ask(
                    _build_read_only_subagent_prompt(description=description, prompt=prompt)
                    if read_only
                    else f"{description}\n\n{prompt}",
                    sink=self._build_background_task_sink(task.id),
                    allowed_tool_names=READ_ONLY_SUBAGENT_TOOL_NAMES if read_only else None,
                    allowed_bash_command_prefixes=READ_ONLY_SUBAGENT_BASH_PREFIXES if read_only else None,
                    require_read_only_subagents=read_only,
                )
                self.task_manager.complete(task.id, output)
            except Exception as exc:  # noqa: BLE001
                self.task_manager.fail(task.id, f"{type(exc).__name__}: {exc}")

        Thread(target=worker, daemon=True).start()
        return task.id

    def describe_tools(self) -> str:
        lines = []
        for tool in self.tools:
            mode = "read-only" if tool.read_only else "write"
            concurrency = "parallel" if tool.concurrency_safe else "serial"
            risk = tool.declared_risk_level()
            lines.append(f"{tool.name}: {tool.description} [{mode}, {concurrency}, risk={risk}]")
        return "\n".join(lines)

    def describe_tasks(self) -> str:
        tasks = self.task_manager.list()
        latest_artifact = self.active_planning_artifact()
        lines = ["planning lifecycle:"]
        lines.extend(self.describe_planning_lifecycle())
        if not tasks:
            lines.append("")
            lines.append("No tasks.")
            return "\n".join(lines)
        grouped: list[tuple[str, list[Any]]] = []
        scout_tasks = [task for task in tasks if task.metadata.get("task_role") == "scout"]
        execution_tasks = [
            task
            for task in tasks
            if task.metadata.get("task_role") == "execution"
            and (
                latest_artifact is None
                or task.metadata.get("active_plan_id") == latest_artifact.artifact_id
            )
        ]
        other_tasks = [task for task in tasks if task not in scout_tasks and task not in execution_tasks]
        if scout_tasks:
            grouped.append(("scout tasks:", scout_tasks))
        if execution_tasks:
            grouped.append(("execution tasks following active plan:", execution_tasks))
        if other_tasks:
            grouped.append(("other tasks:", other_tasks))
        for header, task_group in grouped:
            lines.append("")
            lines.append(header)
            for task in task_group:
                updated = task.updated_at or task.created_at
                summary = f"  progress={task.progress_summary}" if task.progress_summary else ""
                metadata_bits = []
                if latest_artifact is not None and task.id in latest_artifact.task_ids:
                    metadata_bits.append("active_plan_task=yes")
                if task.metadata.get("task_role"):
                    metadata_bits.append(f"task_role={task.metadata['task_role']}")
                if task.metadata.get("planner_kind"):
                    metadata_bits.append(f"planner={task.metadata['planner_kind']}")
                if task.metadata.get("scout_category"):
                    metadata_bits.append(f"scout={task.metadata['scout_category']}")
                if task.metadata.get("active_plan_id"):
                    metadata_bits.append(f"plan={task.metadata['active_plan_id']}")
                metadata_suffix = f"  {' '.join(metadata_bits)}" if metadata_bits else ""
                lines.append(
                    f"{task.id}  status={task.status}  kind={task.kind}  updated={updated}  "
                    f"description={task.description}{summary}{metadata_suffix}"
                )
        return "\n".join(lines)

    def describe_mcp_servers(self) -> str:
        config_path = self.config.mcp_config_path
        header = f"config_path: {config_path}" if config_path is not None else "config_path: (none)"
        if self.mcp_registry is None or not self.mcp_registry.list_servers():
            return header + "\nNo MCP servers configured."
        summary = self._mcp_health_summary()
        lines = []
        for server_name in self.mcp_registry.list_servers():
            server = self.mcp_registry.get_server(server_name)
            version = (
                server.initialize_result.server_version
                if server.initialize_result is not None
                else "unknown"
            )
            line = (
                f"{server_name}: transport={server.client.config.transport} "
                f"status={server.status} version={version} tools={len(server.tools)}"
            )
            if server.client.config.auth_mode:
                line += f" auth={server.client.config.auth_mode}"
            if server.last_error:
                line += f" error={server.last_error}"
            if server.last_connected_at:
                line += f" connected_at={server.last_connected_at}"
            if server.last_failed_at:
                line += f" failed_at={server.last_failed_at}"
            if server.failure_count:
                line += f" failures={server.failure_count}"
            retry_in = self.mcp_registry.retry_wait_seconds(server_name)
            if retry_in:
                line += f" retry_in={retry_in}s"
            lines.append(line)
        return "\n".join([header, summary, *lines])

    def describe_mcp_tools(self) -> str:
        if self.mcp_registry is None:
            return "No MCP servers configured."
        refs = self.mcp_registry.list_tool_references()
        if not refs:
            return "No MCP tools loaded."
        lines = []
        for ref in refs:
            mapped_name = make_mcp_tool_name(ref.server_name, ref.tool.name)
            lines.append(
                f"{ref.qualified_name} -> {mapped_name}: {ref.tool.description}"
            )
        return "\n".join(lines)

    def diagnose_mcp_tool(
        self,
        server_name: str,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
    ) -> McpDiagnosticResult:
        if self.mcp_registry is None or server_name not in self.mcp_registry.list_servers():
            return McpDiagnosticResult(
                server_name=server_name,
                tool_name=tool_name,
                ok=False,
                source="config",
                error_text=f'MCP server "{server_name}" is not configured.',
            )

        registry = self.mcp_registry
        server = self.ensure_mcp_server_connected(server_name)
        assert server is not None
        transport = server.client.config.transport
        retry_in = registry.retry_wait_seconds(server_name)
        if server.status != "connected":
            return McpDiagnosticResult(
                server_name=server_name,
                tool_name=tool_name,
                ok=False,
                source="transport",
                transport=transport,
                server_status=server.status,
                retry_in=retry_in,
                failure_count=server.failure_count,
                error_text=server.last_error or f'MCP server "{server_name}" is unavailable.',
            )

        reference = registry.find_tool_reference(server_name, tool_name)
        if reference is None:
            return McpDiagnosticResult(
                server_name=server_name,
                tool_name=tool_name,
                ok=False,
                source="mcp_server",
                transport=transport,
                server_status=server.status,
                failure_count=server.failure_count,
                error_text=f'MCP tool "{tool_name}" is not available on server "{server_name}".',
            )

        try:
            result = server.client.call_tool(tool_name, arguments or {})
        except (McpClientError, OSError, TimeoutError) as exc:
            self.handle_mcp_server_failure(server_name, f"{type(exc).__name__}: {exc}")
            failed = registry.get_server(server_name)
            return McpDiagnosticResult(
                server_name=server_name,
                tool_name=tool_name,
                ok=False,
                source="transport",
                transport=transport,
                server_status=failed.status,
                retry_in=registry.retry_wait_seconds(server_name),
                failure_count=failed.failure_count,
                error_text=f"{type(exc).__name__}: {exc}",
            )

        rendered = _render_mcp_content(result.content)
        if result.is_error:
            return McpDiagnosticResult(
                server_name=server_name,
                tool_name=tool_name,
                ok=False,
                source="mcp_tool",
                transport=transport,
                server_status=server.status,
                retry_in=retry_in,
                failure_count=server.failure_count,
                result_text=rendered,
                error_text="MCP tool returned an error payload.",
            )
        return McpDiagnosticResult(
            server_name=server_name,
            tool_name=tool_name,
            ok=True,
            source="ok",
            transport=transport,
            server_status=server.status,
            retry_in=retry_in,
            failure_count=server.failure_count,
            result_text=rendered,
        )

    def describe_mcp_tool_diagnostic(
        self,
        server_name: str,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
    ) -> str:
        result = self.diagnose_mcp_tool(server_name, tool_name, arguments=arguments)
        lines = [
            f"server: {result.server_name}",
            f"tool: {result.tool_name}",
            f"ok: {'yes' if result.ok else 'no'}",
            f"source: {result.source}",
        ]
        if result.transport:
            lines.append(f"transport: {result.transport}")
        if result.server_status:
            lines.append(f"server_status: {result.server_status}")
        if result.failure_count:
            lines.append(f"failure_count: {result.failure_count}")
        if result.retry_in:
            lines.append(f"retry_in: {result.retry_in}s")
        if result.error_text:
            lines.append(f"error: {result.error_text}")
        if result.result_text:
            lines.append("result:")
            lines.append(result.result_text)
        next_steps = self._mcp_next_steps(result.source, server_name=server_name, tool_name=tool_name)
        if next_steps:
            lines.append("next_steps:")
            lines.extend(f"- {step}" for step in next_steps)
        return "\n".join(lines)

    def verify_mcp_tool_via_model(
        self,
        server_name: str,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
    ) -> McpVerificationResult:
        preflight = self.diagnose_mcp_tool(server_name, tool_name, arguments=arguments)
        mapped_tool_name = make_mcp_tool_name(server_name, tool_name)
        if not preflight.ok:
            return McpVerificationResult(
                server_name=server_name,
                tool_name=tool_name,
                mapped_tool_name=mapped_tool_name,
                ok=False,
                source=preflight.source,
                error_text=preflight.error_text or "MCP preflight failed.",
                preflight=preflight,
            )

        capabilities = getattr(self.provider, "capabilities", None)
        if capabilities is None or not capabilities.supports_tool_calling:
            return McpVerificationResult(
                server_name=server_name,
                tool_name=tool_name,
                mapped_tool_name=mapped_tool_name,
                ok=False,
                source="model",
                error_text="Current model/provider does not support tool calling.",
                preflight=preflight,
            )

        payload = arguments or {}
        prompt = (
            "You must call the MCP tool exactly once.\n"
            f"Tool name: {mapped_tool_name}\n"
            f"Arguments JSON: {payload}\n"
            "Do not guess or answer without using the tool. "
            "After receiving the tool result, briefly summarize it."
        )
        events: list[RuntimeEvent] = []
        try:
            output = self.ask(prompt, sink=events.append)
        except Exception as exc:  # noqa: BLE001
            return McpVerificationResult(
                server_name=server_name,
                tool_name=tool_name,
                mapped_tool_name=mapped_tool_name,
                ok=False,
                source="model",
                error_text=f"{type(exc).__name__}: {exc}",
                preflight=preflight,
            )

        tool_called = any(
            event.kind == "tool_started" and event.tool_name == mapped_tool_name
            for event in events
        )
        if not tool_called:
            return McpVerificationResult(
                server_name=server_name,
                tool_name=tool_name,
                mapped_tool_name=mapped_tool_name,
                ok=False,
                source="model",
                output_text=output,
                error_text="Model did not invoke the required MCP tool.",
                tool_called=False,
                preflight=preflight,
            )

        for event in events:
            if event.kind == "tool_failed" and event.tool_name == mapped_tool_name:
                source = "transport"
                if self.mcp_registry is not None and server_name in self.mcp_registry.list_servers():
                    server = self.mcp_registry.get_server(server_name)
                    if server.status == "connected":
                        source = "mcp_server"
                return McpVerificationResult(
                    server_name=server_name,
                    tool_name=tool_name,
                    mapped_tool_name=mapped_tool_name,
                    ok=False,
                    source=source,
                    output_text=output,
                    error_text=event.message,
                    tool_called=True,
                    preflight=preflight,
                )

        return McpVerificationResult(
            server_name=server_name,
            tool_name=tool_name,
            mapped_tool_name=mapped_tool_name,
            ok=True,
            source="ok",
            output_text=output,
            tool_called=True,
            preflight=preflight,
        )

    def describe_mcp_verification(
        self,
        server_name: str,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
    ) -> str:
        result = self.verify_mcp_tool_via_model(server_name, tool_name, arguments=arguments)
        lines = [
            f"server: {result.server_name}",
            f"tool: {result.tool_name}",
            f"mapped_tool: {result.mapped_tool_name}",
            f"ok: {'yes' if result.ok else 'no'}",
            f"source: {result.source}",
            f"tool_called: {'yes' if result.tool_called else 'no'}",
        ]
        if result.preflight is not None:
            lines.extend(
                [
                    "preflight:",
                    f"  ok: {'yes' if result.preflight.ok else 'no'}",
                    f"  source: {result.preflight.source}",
                ]
            )
            if result.preflight.transport:
                lines.append(f"  transport: {result.preflight.transport}")
            if result.preflight.server_status:
                lines.append(f"  server_status: {result.preflight.server_status}")
            if result.preflight.failure_count:
                lines.append(f"  failure_count: {result.preflight.failure_count}")
            if result.preflight.retry_in:
                lines.append(f"  retry_in: {result.preflight.retry_in}s")
        if result.error_text:
            lines.append(f"error: {result.error_text}")
        if result.output_text:
            lines.append("output:")
            lines.append(result.output_text)
        next_steps = self._mcp_next_steps(
            result.source,
            server_name=server_name,
            tool_name=tool_name,
            model_verification=True,
        )
        if next_steps:
            lines.append("next_steps:")
            lines.extend(f"- {step}" for step in next_steps)
        return "\n".join(lines)

    def describe_history(self, limit: int = 12) -> str:
        if not self.state.messages and not self.state.context_summary:
            return "No messages yet."

        visible_messages = self.state.messages[-limit:]
        lines = []
        if self.state.context_summary:
            summary = self.state.context_summary
            if len(summary) > 180:
                summary = summary[:177] + "..."
            lines.append(f"context_summary: {summary}")
        for index, message in enumerate(visible_messages, start=1):
            role = message.get("role", "unknown")
            summary = self._summarize_message(message)
            lines.append(f"{index}. {role}: {summary}")
        return "\n".join(lines)

    def describe_recent_changes(self, limit: int = 5) -> str:
        changes = self.state.recent_change_sets[-limit:]
        redos = self.state.undone_change_sets[-limit:]
        if not changes and not redos:
            return "No recorded workspace changes."
        lines = []
        if changes:
            lines.append("Undo stack:")
            for index, change in enumerate(reversed(changes), start=1):
                lines.append(
                    f"{index}. {change.change_id}  tool={change.tool_name}  files={len(change.files)}  "
                    f"created={change.created_at}"
                )
                lines.append(f"   summary: {change.summary}")
                for file_change in change.files[:3]:
                    lines.append("   - " + self._render_file_change_summary(file_change))
                if len(change.files) > 3:
                    lines.append(f"   - ... {len(change.files) - 3} more file(s)")
        if redos:
            if lines:
                lines.append("")
            lines.append("Redo stack:")
            for index, change in enumerate(reversed(redos), start=1):
                lines.append(
                    f"{index}. {change.change_id}  tool={change.tool_name}  files={len(change.files)}  "
                    f"created={change.created_at}"
                )
                lines.append(f"   summary: {change.summary}")
        return "\n".join(lines)

    def recent_change_entries(self, limit: int = 5) -> list[str]:
        changes = list(reversed(self.state.recent_change_sets[-limit:]))
        return [self._render_change_entry(change) for change in changes]

    def recent_redo_entries(self, limit: int = 5) -> list[str]:
        changes = list(reversed(self.state.undone_change_sets[-limit:]))
        return [self._render_change_entry(change) for change in changes]

    def selected_change_file_count(self, *, index: int = 0, limit: int = 5, redo: bool = False) -> int:
        stack = self.state.undone_change_sets if redo else self.state.recent_change_sets
        visible = list(reversed(stack[-limit:]))
        if not visible:
            return 0
        selected = visible[max(0, min(index, len(visible) - 1))]
        return len(selected.files)

    def selected_change_detail(
        self,
        *,
        index: int = 0,
        file_index: int = 0,
        limit: int = 5,
        redo: bool = False,
    ) -> str:
        stack = self.state.undone_change_sets if redo else self.state.recent_change_sets
        visible = list(reversed(stack[-limit:]))
        if not visible:
            return "No selected change."
        selected = visible[max(0, min(index, len(visible) - 1))]
        counts = self._count_change_actions(selected)
        clamped_file_index = max(0, min(file_index, len(selected.files) - 1)) if selected.files else 0
        lines = [
            f"change: {selected.change_id}",
            f"tool: {selected.tool_name}",
            f"files: {len(selected.files)}",
            (
                "actions: "
                f"create={counts['create']} "
                f"update={counts['update']} "
                f"delete={counts['delete']}"
            ),
            f"summary: {selected.summary}",
        ]
        if selected.files:
            lines.append("")
            lines.append("")
            lines.append("Files")
            for current_index, file_change in enumerate(selected.files[:8], start=1):
                marker = ">" if current_index - 1 == clamped_file_index else " "
                lines.append(f"{marker} {current_index}. {self._describe_file_change(file_change)}")
            if len(selected.files) > 8:
                lines.append(f"  ... {len(selected.files) - 8} more file(s)")
            focused = selected.files[clamped_file_index]
            lines.append("")
            lines.append(f"Focused file ({clamped_file_index + 1}/{len(selected.files)})")
            lines.append(self._render_file_change_detail(focused))
        return "\n".join(lines)

    def describe_provider(self) -> str:
        capabilities = getattr(self.provider, "capabilities", None)
        if capabilities is None:
            return (
                f"provider: {self.config.provider}\n"
                f"model: {self.config.model}\n"
                "notes: provider does not declare capabilities"
            )
        return format_capabilities(capabilities)

    def describe_project_memory(self) -> str:
        if not self.project_context.memory_content:
            return "No project memory loaded."
        memory_path = self.project_context.memory_path
        header = f"path: {memory_path}" if memory_path is not None else "path: (unknown)"
        return f"{header}\n{self.project_context.memory_content}"

    def describe_loaded_skills(self) -> str:
        if not self.project_context.skills:
            return "No project skills loaded."
        lines = []
        manual_enabled_names = set(self.state.enabled_skill_names)
        manual_disabled_names = set(self.state.disabled_skill_names)
        for skill in self.project_context.skills:
            status_parts = []
            if skill.name in manual_disabled_names:
                status_parts.append("disabled")
            elif skill.name in manual_enabled_names:
                status_parts.append("enabled")
                status_parts.append("manual")
            elif skill.auto_enable:
                status_parts.append("enabled")
                status_parts.append("auto")
            else:
                status_parts.append("inactive")
            status = ",".join(status_parts)
            preview = skill.content
            if len(preview) > 140:
                preview = preview[:137] + "..."
            description = f" description={skill.description}" if skill.description else ""
            tags = f" tags={','.join(skill.tags)}" if skill.tags else ""
            lines.append(
                f"{skill.name}: status={status} path={skill.path}{description}{tags} content={preview}"
            )
        return "\n".join(lines)

    def describe_plugins(self) -> str:
        return self._runtime_context.describe_plugins(self.state)

    def describe_plugin(self, name: str) -> str:
        plugin_name = name.strip()
        if not plugin_name:
            return "Usage: /plugin show <plugin-name>"
        return self.plugin_registry.describe_plugin(plugin_name, self.state)

    def describe_advisor(self) -> str:
        if not self.state.advisor_model or self.state.advisor_mode == "off":
            return (
                "Advisor: not set\n"
                "Mode: off\n"
                'Use "/advisor <model>" to enable or "/advisor mode interactive-review" after a model is set.'
            )
        lines = [
            f"Advisor: {self.state.advisor_model}",
            f"Mode: {self.state.advisor_mode}",
        ]
        if self.state.advisor_mode == "final-review":
            lines.append("Status: final answers are reviewed before they are returned.")
        else:
            lines.append("Status: plan, write-risk, and final-answer checkpoints are reviewed.")
        if self.state.advisor_last_result is not None:
            lines.append(
                "Last review: "
                f"{self.state.advisor_last_result.checkpoint}/{self.state.advisor_last_result.status}"
            )
            if self.state.advisor_last_result.reason:
                lines.append("Last reason: " + self.state.advisor_last_result.reason)
            if self.state.advisor_last_result.risk_flags:
                lines.append("Risk flags: " + ", ".join(self.state.advisor_last_result.risk_flags))
        lines.append("Execution constraints: " + self.state.active_execution_constraint)
        if self.state.constraint_source:
            lines.append("Constraint source: " + self.state.constraint_source)
        if self.state.constraint_reason:
            lines.append("Constraint reason: " + self.state.constraint_reason)
        if self.state.active_execution_plan_id:
            lines.append("Active execution plan: " + self.state.active_execution_plan_id)
        if self.state.last_plan_drift_status:
            lines.append("Last plan drift status: " + self.state.last_plan_drift_status)
        if self.state.last_plan_drift_reason:
            lines.append("Last plan drift reason: " + self.state.last_plan_drift_reason)
        if self.state.last_plan_drift_context:
            lines.append("Last plan drift analysis:")
            lines.extend(
                "  " + line
                for line in self._compact_multiline_text(
                    self.state.last_plan_drift_context,
                    max_lines=10,
                    max_chars=1200,
                ).splitlines()
            )
        active_plan = self.active_planning_artifact()
        if active_plan is not None and active_plan.advisor_risk_flags:
            lines.append("Active plan risk flags: " + ", ".join(active_plan.advisor_risk_flags))
        if active_plan is not None and active_plan.derived_from_drift:
            lines.append("Active plan derived from drift: yes")
        if active_plan is not None and active_plan.derivation_reason:
            lines.append("Active plan derivation reason: " + active_plan.derivation_reason)
        return "\n".join(lines)

    def describe_active_plan(self) -> str:
        artifact = self.active_planning_artifact()
        if artifact is None:
            return "No active planning artifact."
        return self._render_planning_artifact_detail(artifact, active=True)

    def describe_planning_artifacts(self) -> str:
        artifacts = list(reversed(self.state.planning_artifact_history))
        if not artifacts:
            return "No planning artifacts."
        active_id = self.state.active_planning_artifact_id
        lines = ["planning artifacts:"]
        for artifact in artifacts:
            marker = "*" if artifact.artifact_id == active_id else "-"
            relation_bits = []
            if artifact.supersedes_artifact_id:
                relation_bits.append(f"supersedes={artifact.supersedes_artifact_id}")
            if artifact.superseded_by_artifact_id:
                relation_bits.append(f"superseded_by={artifact.superseded_by_artifact_id}")
            if artifact.derived_from_drift:
                relation_bits.append("derived_from_drift=yes")
            relation_suffix = f" {' '.join(relation_bits)}" if relation_bits else ""
            lines.append(
                f"{marker} {artifact.artifact_id} kind={artifact.kind} goal={artifact.goal} "
                f"created_at={artifact.created_at} scouts={len(artifact.scout_categories)} "
                f"tasks={len(artifact.task_ids)}{relation_suffix}"
            )
        return "\n".join(lines)

    def describe_planning_artifact(self, identifier: str) -> str:
        target = identifier.strip() or "latest"
        artifact = self.resolve_planning_artifact(target)
        if artifact is None:
            return f'Unknown planning artifact "{target}".'
        return self._render_planning_artifact_detail(
            artifact,
            active=artifact.artifact_id == self.state.active_planning_artifact_id,
        )

    def use_planning_artifact(self, identifier: str) -> str:
        target = identifier.strip()
        if not target:
            return "Usage: /plan use <artifact-id|latest>"
        artifact = self.resolve_planning_artifact(target)
        if artifact is None:
            return f'Unknown planning artifact "{target}".'
        self.state.active_planning_artifact_id = artifact.artifact_id
        self.persist_state()
        return (
            f'Active planning artifact set to {artifact.artifact_id}.\n'
            f"Goal: {artifact.goal}"
        )

    def revert_to_planning_artifact(self, identifier: str) -> str:
        target = identifier.strip()
        if not target:
            return "Usage: /plan revert <artifact-id|latest>"
        artifact = self.resolve_planning_artifact(target)
        if artifact is None:
            return f'Unknown planning artifact "{target}".'
        self.state.active_planning_artifact_id = artifact.artifact_id
        self.persist_state()
        return (
            f"Reactivated planning artifact {artifact.artifact_id}.\n"
            f"Goal: {artifact.goal}"
        )

    def clear_active_plan(self) -> str:
        if self.state.active_planning_artifact_id is None:
            return "No active planning artifact."
        previous = self.state.active_planning_artifact_id
        self.state.active_planning_artifact_id = None
        self.persist_state()
        return f"Cleared active planning artifact {previous}."

    def _render_planning_artifact_detail(self, artifact: PlanningArtifact, *, active: bool) -> str:
        lines = [
            f"artifact_id: {artifact.artifact_id}",
            f"active: {'yes' if active else 'no'}",
            f"currently_executing: {'yes' if artifact.artifact_id == self.state.active_execution_plan_id else 'no'}",
            f"kind: {artifact.kind}",
            f"goal: {artifact.goal}",
            f"created_at: {artifact.created_at}",
            f"supersedes_artifact_id: {artifact.supersedes_artifact_id or 'none'}",
            f"superseded_by_artifact_id: {artifact.superseded_by_artifact_id or 'none'}",
            f"lineage_position: {self._planning_artifact_lineage_position(artifact)}",
            f"derived_from_drift: {'yes' if artifact.derived_from_drift else 'no'}",
            f"derivation_reason: {artifact.derivation_reason or 'none'}",
            f"used_read_only_subagents: {'yes' if artifact.used_read_only_subagents else 'no'}",
            f"scout_categories: {', '.join(artifact.scout_categories) if artifact.scout_categories else '(none)'}",
            f"task_ids: {', '.join(artifact.task_ids) if artifact.task_ids else '(none)'}",
            f"advisor_status: {artifact.advisor_status or 'none'}",
            (
                "advisor_risk_flags: "
                + (", ".join(artifact.advisor_risk_flags) if artifact.advisor_risk_flags else "(none)")
            ),
            "summary:",
            artifact.summary,
        ]
        advisor_lines = self._render_planning_artifact_advisor_review(artifact)
        if advisor_lines:
            lines.append("")
            lines.append("advisor_review:")
            lines.extend(advisor_lines)
        lineage_lines = self._render_planning_artifact_lineage(artifact)
        if lineage_lines:
            lines.append("")
            lines.append("lineage:")
            lines.extend(lineage_lines)
        comparison_lines = self._render_planning_artifact_comparisons(artifact)
        if comparison_lines:
            lines.append("")
            lines.append("comparisons:")
            lines.extend(comparison_lines)
        if active and self.state.advisor_last_result is not None:
            lines.append("")
            lines.append("latest_session_advisor_review:")
            lines.append(
                f"- {self.state.advisor_last_result.checkpoint}/{self.state.advisor_last_result.status}"
            )
            if self.state.advisor_last_result.reason:
                lines.append(f"- reason: {self.state.advisor_last_result.reason}")
            if self.state.advisor_last_result.risk_flags:
                lines.append("- risk_flags: " + ", ".join(self.state.advisor_last_result.risk_flags))
        drift_lines = self._render_recent_plan_drift_analysis(artifact, active=active)
        if drift_lines:
            lines.append("")
            lines.append("recent_plan_drift_analysis:")
            lines.extend(drift_lines)
        scout_lines = self._render_planning_artifact_scout_outputs(artifact)
        if scout_lines:
            lines.append("")
            lines.append("scout_outputs:")
            lines.extend(scout_lines)
        lines.append("")
        lines.append("next_actions:")
        if active:
            lines.append(f"- /plan derive {artifact.goal}")
        else:
            lines.append(f"- /plan revert {artifact.artifact_id}")
            lines.append(f"- /plan use {artifact.artifact_id}")
        if artifact.supersedes_artifact_id:
            lines.append(f"- /plan show {artifact.supersedes_artifact_id}")
        if artifact.superseded_by_artifact_id:
            lines.append(f"- /plan show {artifact.superseded_by_artifact_id}")
        return "\n".join(lines)

    def _render_planning_artifact_lineage(self, artifact: PlanningArtifact) -> list[str]:
        lineage = self._planning_artifact_lineage(artifact)
        if not lineage:
            return []
        active_id = self.state.active_planning_artifact_id
        lines: list[str] = []
        for item in lineage:
            roles = []
            if item.artifact_id == artifact.artifact_id:
                roles.append("current")
            if item.artifact_id == active_id:
                roles.append("active")
            role_suffix = f" ({', '.join(roles)})" if roles else ""
            lines.append(
                f"- {item.artifact_id}{role_suffix}: goal={item.goal} "
                f"supersedes={item.supersedes_artifact_id or 'none'} "
                f"superseded_by={item.superseded_by_artifact_id or 'none'}"
            )
        return lines

    def _planning_artifact_lineage(self, artifact: PlanningArtifact) -> list[PlanningArtifact]:
        artifact_map = {item.artifact_id: item for item in self.planning_artifacts()}
        root = artifact
        seen: set[str] = set()
        while root.supersedes_artifact_id and root.supersedes_artifact_id in artifact_map and root.artifact_id not in seen:
            seen.add(root.artifact_id)
            root = artifact_map[root.supersedes_artifact_id]
        lineage = [root]
        seen = {root.artifact_id}
        current = root
        while (
            current.superseded_by_artifact_id
            and current.superseded_by_artifact_id in artifact_map
            and current.superseded_by_artifact_id not in seen
        ):
            current = artifact_map[current.superseded_by_artifact_id]
            lineage.append(current)
            seen.add(current.artifact_id)
        return lineage

    def _planning_artifact_lineage_position(self, artifact: PlanningArtifact) -> str:
        lineage = self._planning_artifact_lineage(artifact)
        if not lineage:
            return "unknown"
        for index, item in enumerate(lineage, start=1):
            if item.artifact_id == artifact.artifact_id:
                return f"{index}/{len(lineage)}"
        return f"?/{len(lineage)}"

    def _render_planning_artifact_comparisons(self, artifact: PlanningArtifact) -> list[str]:
        lineage = self._planning_artifact_lineage(artifact)
        if not lineage:
            return []
        try:
            index = next(i for i, item in enumerate(lineage) if item.artifact_id == artifact.artifact_id)
        except StopIteration:
            return []
        lines: list[str] = []
        if index > 0:
            lines.extend(self._render_planning_artifact_comparison("against_previous", lineage[index - 1], artifact))
        if index < len(lineage) - 1:
            lines.extend(self._render_planning_artifact_comparison("against_next", artifact, lineage[index + 1]))
        return lines

    def _render_recent_plan_drift_analysis(
        self,
        artifact: PlanningArtifact,
        *,
        active: bool,
    ) -> list[str]:
        if not active or not self.state.last_plan_drift_context:
            return []
        compact = self._compact_multiline_text(
            self.state.last_plan_drift_context,
            max_lines=12,
            max_chars=1400,
        )
        return [f"- {line}" for line in compact.splitlines()]

    def _render_planning_artifact_comparison(
        self,
        label: str,
        base: PlanningArtifact,
        target: PlanningArtifact,
    ) -> list[str]:
        lines = [f"- {label}: {base.artifact_id} -> {target.artifact_id}"]
        if base.goal != target.goal:
            lines.append(f"  goal_changed: {base.goal} -> {target.goal}")
        if (base.advisor_status or "none") != (target.advisor_status or "none"):
            lines.append(
                "  advisor_status_changed: "
                f"{base.advisor_status or 'none'} -> {target.advisor_status or 'none'}"
            )
        if base.advisor_risk_flags != target.advisor_risk_flags:
            lines.append(
                "  risk_flags_changed: "
                f"{', '.join(base.advisor_risk_flags) if base.advisor_risk_flags else '(none)'}"
                " -> "
                f"{', '.join(target.advisor_risk_flags) if target.advisor_risk_flags else '(none)'}"
            )
        diff_lines = self._summarize_planning_summary_diff(base.summary, target.summary)
        if diff_lines:
            lines.append("  summary_diff:")
            lines.extend(f"    {line}" for line in diff_lines)
        return lines

    def _summarize_planning_summary_diff(self, before: str, after: str) -> list[str]:
        diff = list(
            difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile="before",
                tofile="after",
                lineterm="",
            )
        )
        filtered = [line for line in diff[2:] if line.startswith("+") or line.startswith("-")]
        if not filtered:
            return []
        if len(filtered) > 8:
            filtered = filtered[:8] + [f"... {len(diff[2:]) - 8} more diff line(s)"]
        return filtered

    def _render_planning_artifact_advisor_review(self, artifact: PlanningArtifact) -> list[str]:
        if not artifact.advisor_status:
            return []
        lines = [f"- status: {artifact.advisor_status}"]
        if artifact.advisor_reason:
            lines.append(f"- reason: {artifact.advisor_reason}")
        if artifact.advisor_risk_flags:
            lines.append("- risk_flags: " + ", ".join(artifact.advisor_risk_flags))
        if artifact.advisor_suggested_changes:
            lines.append("- suggested_changes:")
            lines.extend(f"  - {item}" for item in artifact.advisor_suggested_changes)
        return lines

    def _render_planning_artifact_scout_outputs(self, artifact: PlanningArtifact) -> list[str]:
        if not artifact.task_ids:
            return []
        lines: list[str] = []
        for task_id in artifact.task_ids:
            task = self.task_manager.get(task_id)
            if task is None:
                lines.append(f"- {task_id}: missing")
                continue
            lines.append(
                f"- {task.id}: status={task.status} category={task.metadata.get('scout_category', '(unknown)')} "
                f"description={task.description}"
            )
            detail = (task.output or task.error or task.progress_summary or "").strip()
            if not detail:
                lines.append("  (no scout output)")
                continue
            compact = self._compact_multiline_text(detail, max_lines=8, max_chars=900)
            lines.extend(f"  {line}" for line in compact.splitlines())
        return lines

    def has_advisor_model(self) -> bool:
        if self.state.advisor_model and self.state.advisor_mode == "off":
            self._normalize_advisor_state()
        return bool(self.state.advisor_model and self.state.advisor_mode != "off")

    def uses_interactive_advisor(self) -> bool:
        if self.state.advisor_model and self.state.advisor_mode == "off":
            self._normalize_advisor_state()
        return self.has_advisor_model() and self.state.advisor_mode == "interactive-review"

    def build_advisor_provider(self):
        if not self.has_advisor_model():
            return None
        return build_provider(
            provider=self.config.provider,
            model=self.state.advisor_model,
            max_tokens=min(self.config.max_tokens, 2048),
            api_key=self.config.api_key,
            base_url=self.config.base_url,
        )

    def build_advisor_review_prompt(
        self,
        *,
        checkpoint: str,
        user_prompt: str,
        candidate_text: str,
        pending_tool_names: tuple[str, ...] = (),
        active_plan: PlanningArtifact | None = None,
        plan_drift_context: str | None = None,
    ) -> str:
        context_lines = self._advisor_context_lines(exclude_latest_assistant=True)
        pending_tools = ", ".join(pending_tool_names) if pending_tool_names else "(none)"
        parts = [
            "You are the advisor model for a coding assistant.",
            "Review the candidate work and return exactly one JSON object.",
            'JSON schema: {"status":"approve|revise|block","reason":"...","suggested_changes":["..."],"risk_flags":["..."]}',
            "Use block only for clearly unsafe or seriously flawed plans.",
            "",
            f"Checkpoint: {checkpoint}",
            "",
            "Task request:",
            user_prompt.strip() or "(empty)",
            "",
        ]
        if active_plan is not None:
            parts.extend(
                [
                    "Active execution plan:",
                    self._render_active_plan_for_advisor(active_plan),
                    "",
                ]
            )
        if plan_drift_context:
            parts.extend(
                [
                    "Plan drift analysis:",
                    plan_drift_context,
                    "",
                ]
            )
        parts.extend(
            [
            "Conversation context:",
            "\n".join(context_lines) if context_lines else "(none)",
            "",
            f"Pending tools: {pending_tools}",
            "",
            "Candidate work to review:",
            candidate_text.strip(),
            "",
            "Return concise, concrete output. suggested_changes and risk_flags may be empty lists.",
            ]
        )
        return "\n".join(parts)

    def build_advisor_revision_prompt(
        self,
        *,
        user_prompt: str,
        draft_text: str,
        advisor_feedback: str,
    ) -> str:
        context_lines = self._advisor_context_lines(exclude_latest_assistant=True)
        parts = [
            "Revise the final answer using the advisor feedback below.",
            "Return only the revised final answer.",
            "Do not call tools.",
            "",
            "Original user request:",
            user_prompt.strip() or "(empty)",
            "",
            "Conversation context:",
            "\n".join(context_lines) if context_lines else "(none)",
            "",
            "Current draft:",
            draft_text.strip(),
            "",
            "Advisor feedback:",
            advisor_feedback.strip(),
        ]
        return "\n".join(parts)

    def build_advisor_followup_prompt(
        self,
        *,
        checkpoint: str,
        advisor_review: AdvisorReviewSummary,
        pending_tool_names: tuple[str, ...] = (),
        active_plan: PlanningArtifact | None = None,
    ) -> str:
        lines = [
            "Advisor review requires you to revise the current approach before continuing.",
            f"Checkpoint: {checkpoint}",
            f"Status: {advisor_review.status}",
            f"Reason: {advisor_review.reason or '(none provided)'}",
        ]
        if active_plan is not None:
            lines.append(f"Active plan to align with: {active_plan.artifact_id} ({active_plan.goal})")
        if advisor_review.suggested_changes:
            lines.append("Suggested changes:")
            lines.extend(f"- {item}" for item in advisor_review.suggested_changes)
        if advisor_review.risk_flags:
            lines.append("Risk flags:")
            lines.extend(f"- {item}" for item in advisor_review.risk_flags)
        if pending_tool_names:
            lines.append("Pending tools to reconsider: " + ", ".join(pending_tool_names))
        lines.extend(
            [
                "",
                "Revise the plan or response now.",
                "If tools are still needed after revision, call them only after the revised approach is explicit.",
            ]
        )
        return "\n".join(lines)

    def _render_active_plan_for_advisor(self, artifact: PlanningArtifact) -> str:
        lines = [
            f"artifact_id: {artifact.artifact_id}",
            f"kind: {artifact.kind}",
            f"goal: {artifact.goal}",
            "summary:",
            self._compact_multiline_text(artifact.summary, max_lines=10, max_chars=1200),
        ]
        if artifact.advisor_status:
            lines.append(f"advisor_status: {artifact.advisor_status}")
        if artifact.advisor_risk_flags:
            lines.append("advisor_risk_flags: " + ", ".join(artifact.advisor_risk_flags))
        if artifact.scout_categories:
            lines.append("scout_categories: " + ", ".join(artifact.scout_categories))
        if artifact.task_ids:
            lines.append("task_ids: " + ", ".join(artifact.task_ids))
        return "\n".join(lines)

    def build_plan_drift_review_context(
        self,
        *,
        active_plan: PlanningArtifact,
        candidate_text: str,
        pending_tool_names: tuple[str, ...] = (),
    ) -> str:
        lines = [
            f"active_plan_goal: {active_plan.goal}",
            "candidate_work_summary:",
            self._compact_multiline_text(candidate_text.strip(), max_lines=8, max_chars=1000),
        ]
        if pending_tool_names:
            lines.append("pending_tools: " + ", ".join(pending_tool_names))
        if active_plan.advisor_risk_flags:
            lines.append("active_plan_risk_flags: " + ", ".join(active_plan.advisor_risk_flags))
        diff_lines = self._summarize_planning_summary_diff(active_plan.summary, candidate_text)
        if diff_lines:
            lines.append("active_plan_vs_candidate_diff:")
            lines.extend(diff_lines)
        return "\n".join(lines)

    def record_plan_drift_context(self, context: str) -> None:
        compact = self._compact_multiline_text(context.strip(), max_lines=16, max_chars=1800)
        self.state.last_plan_drift_context = compact or None

    def describe_config(self) -> str:
        connected_servers = 0
        failed_servers = 0
        retrying_servers = 0
        if self.mcp_registry is not None:
            for server_name in self.mcp_registry.list_servers():
                server = self.mcp_registry.get_server(server_name)
                if server.status == "connected":
                    connected_servers += 1
                if server.status == "failed":
                    failed_servers += 1
                    retrying_servers += 1
                elif self.mcp_registry.retry_wait_seconds(server_name):
                    retrying_servers += 1
        lines = [
            f"cwd: {self.config.cwd}",
            f"original_cwd: {self.state.original_cwd or self.config.transcript_cwd or self.config.cwd}",
            f"effective_cwd: {self.state.effective_cwd or self.config.cwd}",
            f"workspace_mode: {self.state.workspace_mode}",
            f"provider: {self.config.provider}",
            f"model: {self.config.model}",
            f"mcp_config_path: {self.config.mcp_config_path}",
            f"mcp_servers: {0 if self.mcp_registry is None else len(self.mcp_registry.list_servers())}",
            f"mcp_connected_servers: {connected_servers}",
            f"mcp_failed_servers: {failed_servers}",
            f"mcp_retrying_servers: {retrying_servers}",
            f"project_memory: {'loaded' if self.project_context.memory_content else 'none'}",
            f"advisor_model: {self.state.advisor_model or 'none'}",
            f"advisor_mode: {self.state.advisor_mode}",
            f"advisor_reviews: {len(self.state.advisor_review_history)}",
            f"advisor_blocks: {self.advisor_block_count()}",
            f"planning_artifacts: {len(self.planning_artifacts())}",
            "active_planning_artifact_id: " + str(self.state.active_planning_artifact_id or "none"),
            f"project_plugins: {len(self.plugin_registry.list_plugins())}",
            f"enabled_plugins: {len(self.plugin_registry.enabled_plugins(self.state))}",
            f"manual_enabled_plugins: {len(self.state.enabled_plugin_names)}",
            f"manual_disabled_plugins: {len(self.state.disabled_plugin_names)}",
            f"project_skills: {len(self.project_context.skills)}",
            f"enabled_skills: {len(self.active_skills())}",
            f"manual_enabled_skills: {len(self.state.enabled_skill_names)}",
            f"manual_disabled_skills: {len(self.state.disabled_skill_names)}",
            f"recent_change_sets: {len(self.state.recent_change_sets)}",
            f"redo_change_sets: {len(self.state.undone_change_sets)}",
            f"permission_mode: {self.config.permission_mode}",
            f"execution_constraints: {self.state.active_execution_constraint}",
            f"last_plan_drift_summary: {self._recent_plan_drift_summary() or 'none'}",
            f"max_tokens: {self.config.max_tokens}",
            f"max_turns: {self.config.max_turns}",
            f"max_tool_rounds_per_turn: {self.config.max_tool_rounds_per_turn}",
            f"max_history_messages: {self.config.max_history_messages}",
            f"history_keep_last_messages: {self.config.history_keep_last_messages}",
            f"max_context_summary_chars: {self.config.max_context_summary_chars}",
            f"session_id: {self.state.session_id}",
        ]
        lines.extend(self.describe_planning_lifecycle()[2:])
        return "\n".join(lines)

    def describe_saved_sessions(self, limit: int = 10) -> str:
        transcripts = list_transcripts(self.config.transcript_cwd or self.config.cwd, limit=limit)
        if not transcripts:
            return "No saved sessions."
        lines = []
        for item in transcripts:
            updated = item.updated_at or item.created_at or "unknown"
            provider = item.provider or "unknown"
            model = item.model or "unknown"
            summary_flag = "yes" if item.context_summary_present else "no"
            workspace_mode = item.workspace_mode or "main"
            effective_cwd = item.effective_cwd or item.cwd or "-"
            lines.append(
                f"{item.session_id}  updated={updated}  provider={provider}  "
                f"model={model}  workspace={workspace_mode}  cwd={effective_cwd}  "
                f"messages={item.message_count}  compacted={summary_flag}"
            )
        return "\n".join(lines)

    def get_python_symbol_index(self) -> PythonProjectIndex:
        return self._runtime_context.get_python_symbol_index()

    def get_js_ts_symbol_index(self) -> JsTsProjectIndex:
        return self._runtime_context.get_js_ts_symbol_index()

    def locate_symbol(
        self,
        symbol: str,
        *,
        path: str = ".",
        max_results: int = 50,
    ) -> SymbolLookupResult:
        base = resolve_workspace_path(self.config.cwd, path)
        matches = self._runtime_context.locate_symbols(
            symbol,
            base=base,
            max_results=max_results,
        )
        return SymbolLookupResult(symbol=symbol, matches=matches)

    def collect_references(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "auto",
        max_results: int = 100,
    ) -> ReferenceLookupResult:
        rendered = FindReferencesTool().execute(
            {
                "symbol": symbol,
                "path": path,
                "scope": scope,
                "max_results": max_results,
            },
            self.tool_context(),
        )
        if rendered == "No references found.":
            return ReferenceLookupResult(symbol=symbol, references=())
        references = []
        for line in rendered.splitlines():
            parsed = parse_reference_line(symbol, line)
            if parsed is not None:
                references.append(parsed)
        return ReferenceLookupResult(symbol=symbol, references=tuple(references))

    def build_open_file_target(
        self,
        path: str,
        *,
        line: int = 1,
        column: int = 1,
        end_line: int | None = None,
        end_column: int | None = None,
        label: str = "",
    ) -> EditorTarget:
        resolved = resolve_workspace_path(self.config.cwd, path)
        rel_path = resolved.relative_to(self.config.cwd).as_posix()
        return build_open_file_target(
            rel_path,
            line=line,
            column=column,
            end_line=end_line,
            end_column=end_column,
            label=label,
        )

    def build_symbol_target(
        self,
        symbol: str,
        *,
        path: str = ".",
        match_index: int = 0,
    ) -> EditorTarget:
        lookup = self.locate_symbol(symbol, path=path)
        if not lookup.matches:
            raise FileNotFoundError(f'No symbol definitions found for "{symbol}".')
        if match_index < 0 or match_index >= len(lookup.matches):
            raise IndexError(
                f"match_index {match_index} is out of range for {len(lookup.matches)} symbol match(es)."
            )
        return build_symbol_target(lookup.matches[match_index])

    def build_diff_targets(self, path: str, *, before: str, after: str) -> DiffTargetResult:
        resolved = resolve_workspace_path(self.config.cwd, path)
        rel_path = resolved.relative_to(self.config.cwd).as_posix()
        return build_diff_targets(rel_path, before, after)

    def build_reference_targets(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "auto",
        max_results: int = 100,
    ) -> ReferenceTargetResult:
        lookup = self.collect_references(
            symbol,
            path=path,
            scope=scope,
            max_results=max_results,
        )
        return build_reference_targets(lookup)

    def build_symbol_action_bundle(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "workspace",
        max_definition_results: int = 50,
        max_reference_results: int = 100,
    ) -> SymbolActionBundle:
        lookup = self.locate_symbol(symbol, path=path, max_results=max_definition_results)
        reference_targets = self.build_reference_targets(
            symbol,
            path=path,
            scope=scope,
            max_results=max_reference_results,
        )
        return build_symbol_action_bundle(lookup, reference_targets)

    def clear_history(self) -> None:
        self.state.messages.clear()
        self.state.context_summary = None
        self.persist_state()

    def record_workspace_change(
        self,
        *,
        tool_name: str,
        summary: str,
        file_changes: list[WorkspaceFileChange],
    ) -> None:
        if not file_changes:
            return
        self.state.recent_change_sets.append(
            WorkspaceChangeSet(
                tool_name=tool_name,
                summary=summary,
                files=file_changes,
            )
        )
        self.state.recent_change_sets = self.state.recent_change_sets[-10:]
        self.state.undone_change_sets.clear()
        self.persist_state()

    def undo_last_change(self, args: str = "") -> str:
        if not self.state.recent_change_sets:
            return "No recorded workspace changes to undo."
        selection = self._select_changes(
            self.state.recent_change_sets,
            args=args,
            command="/undo",
        )
        if isinstance(selection, str):
            return selection
        undone: list[WorkspaceChangeSet] = []
        for change in selection:
            self.state.recent_change_sets.remove(change)
            self._apply_change_set(change, direction="undo")
            self.state.undone_change_sets.append(change)
            undone.append(change)
        self.state.undone_change_sets = self.state.undone_change_sets[-10:]
        self.persist_state()
        return self._summarize_change_operation("Undid", undone)

    def redo_last_undo(self, args: str = "") -> str:
        if not self.state.undone_change_sets:
            return "No undone workspace changes to redo."
        selection = self._select_changes(
            self.state.undone_change_sets,
            args=args,
            command="/redo",
        )
        if isinstance(selection, str):
            return selection
        redone: list[WorkspaceChangeSet] = []
        for change in selection:
            self.state.undone_change_sets.remove(change)
            self._apply_change_set(change, direction="redo")
            self.state.recent_change_sets.append(change)
            redone.append(change)
        self.state.recent_change_sets = self.state.recent_change_sets[-10:]
        self.persist_state()
        return self._summarize_change_operation("Redid", redone)

    def reload_project_context(self) -> str:
        self._runtime_context.reload_project_context(self.state)
        self._reconcile_plugin_state()
        self._reconcile_skill_state()
        self._refresh_command_registry()
        self.persist_state()
        memory_status = "loaded" if self.project_context.memory_content else "none"
        return (
            "Reloaded project context. "
            f"memory={memory_status} skills={len(self.project_context.skills)} "
            f"enabled={len(self.active_skills())}"
        )

    def enable_plugin(self, name: str) -> str:
        plugin_name = name.strip()
        if not plugin_name:
            return "Usage: /plugin-enable <plugin-name>"
        plugin = self.plugin_registry.get_plugin(plugin_name)
        if plugin is None:
            return f'Unknown plugin "{plugin_name}".'
        if plugin.name in self.state.disabled_plugin_names:
            self.state.disabled_plugin_names = [
                item for item in self.state.disabled_plugin_names if item != plugin.name
            ]
        if plugin.name not in self.state.enabled_plugin_names:
            self.state.enabled_plugin_names.append(plugin.name)
        self._refresh_plugin_runtime()
        self.persist_state()
        return f'Enabled plugin "{plugin.name}".'

    def disable_plugin(self, name: str) -> str:
        plugin_name = name.strip()
        if not plugin_name:
            return "Usage: /plugin-disable <plugin-name>"
        plugin = self.plugin_registry.get_plugin(plugin_name)
        if plugin is None:
            return f'Unknown plugin "{plugin_name}".'
        self.state.enabled_plugin_names = [
            item for item in self.state.enabled_plugin_names if item != plugin.name
        ]
        if plugin.name not in self.state.disabled_plugin_names:
            self.state.disabled_plugin_names.append(plugin.name)
        self._refresh_plugin_runtime()
        self.persist_state()
        return f'Disabled plugin "{plugin.name}".'

    def set_advisor_model(self, model: str, *, mode: str | None = None) -> str:
        advisor_model = model.strip()
        if not advisor_model:
            return "Usage: /advisor <model>"
        self.state.advisor_model = advisor_model
        self.state.advisor_mode = mode or (
            self.state.advisor_mode if self.state.advisor_mode in {"final-review", "interactive-review"} else "final-review"
        )
        self.persist_state()
        return (
            f"Advisor set to {advisor_model}.\n"
            f"Mode: {self.state.advisor_mode}"
        )

    def set_advisor_mode(self, mode: str) -> str:
        advisor_mode = mode.strip().lower()
        if advisor_mode not in ADVISOR_MODES:
            return "Usage: /advisor mode <final-review|interactive-review>"
        if advisor_mode == "off":
            return self.unset_advisor_model()
        if not self.state.advisor_model:
            return 'Set a model first with "/advisor <model>".'
        self.state.advisor_mode = advisor_mode
        self.persist_state()
        return (
            f"Advisor mode set to {advisor_mode}.\n"
            f"Advisor: {self.state.advisor_model}"
        )

    def unset_advisor_model(self) -> str:
        if self.state.advisor_model is None and self.state.advisor_mode == "off":
            return "Advisor already unset."
        previous = self.state.advisor_model
        self.state.advisor_model = None
        self.state.advisor_mode = "off"
        self.persist_state()
        return f"Advisor disabled (was {previous or 'unset'})."

    def record_advisor_review(self, review: AdvisorReviewSummary) -> None:
        self.state.advisor_last_result = review
        self.state.advisor_review_history.append(review)
        if len(self.state.advisor_review_history) > MAX_ADVISOR_HISTORY:
            self.state.advisor_review_history = self.state.advisor_review_history[-MAX_ADVISOR_HISTORY:]
        if review.checkpoint == "plan_drift" and review.status in {"revise", "block"}:
            self.state.plan_drift_count += 1
            self.state.last_plan_drift_status = review.status
            self.state.last_plan_drift_reason = review.reason or None

    def record_planning_artifact(self, artifact: PlanningArtifact) -> None:
        if artifact.supersedes_artifact_id:
            for item in self.state.planning_artifact_history:
                if item.artifact_id == artifact.supersedes_artifact_id:
                    item.superseded_by_artifact_id = artifact.artifact_id
                    break
        self.state.planning_artifact_history.append(artifact)
        if len(self.state.planning_artifact_history) > MAX_PLANNING_ARTIFACTS:
            self.state.planning_artifact_history = self.state.planning_artifact_history[-MAX_PLANNING_ARTIFACTS:]
        valid_ids = {item.artifact_id for item in self.state.planning_artifact_history}
        for item in self.state.planning_artifact_history:
            if item.supersedes_artifact_id not in valid_ids:
                item.supersedes_artifact_id = None
            if item.superseded_by_artifact_id not in valid_ids:
                item.superseded_by_artifact_id = None
        self.state.recent_planning_artifacts = list(self.state.planning_artifact_history)
        self.state.active_planning_artifact_id = artifact.artifact_id

    def prepare_plan_derivation(self, goal: str):
        active = self.active_planning_artifact()
        if active is None:
            return 'No active planning artifact. Use "/ultraplan <goal>" first.'
        request = goal.strip() or active.goal
        derived_from_drift = bool(
            self.state.last_plan_drift_status
            and self.state.last_plan_drift_context
            and self.state.active_planning_artifact_id == active.artifact_id
        )
        derivation_reason = (
            self.state.last_plan_drift_reason
            if derived_from_drift
            else ""
        )
        from .commands.prompt_commands import build_ultraplan_execution

        return build_ultraplan_execution(
            self,
            request,
            metadata_extra={
                "supersede_artifact_id": active.artifact_id,
                "derived_from_active_plan": True,
                "derived_from_drift": derived_from_drift,
                "derivation_reason": derivation_reason,
            },
            derivation_context={
                "artifact_id": active.artifact_id,
                "goal": active.goal,
                "summary": active.summary,
                "derivation_reason": derivation_reason,
                "last_plan_drift_context": self.state.last_plan_drift_context or "",
            },
            progress_message=(
                "Revising active plan from drift feedback"
                if derived_from_drift
                else "Revising active plan"
            ),
        )

    def run_ultraplan(
        self,
        *,
        goal: str,
        scout_categories: tuple[str, ...],
        supersede_artifact_id: str | None = None,
        derived_from_drift: bool = False,
        derivation_reason: str | None = None,
        sink=None,
    ) -> str:
        goal_text = goal.strip()
        if not goal_text:
            return "Usage: /ultraplan <goal>"
        parent_artifact = (
            self.resolve_planning_artifact(supersede_artifact_id)
            if supersede_artifact_id
            else None
        )
        scout_definitions = _build_ultraplan_scout_definitions(goal_text, scout_categories)
        start_message_count = len(self.state.messages)
        start_context_summary = self.state.context_summary
        start_planning_count = len(self.state.planning_artifact_history)
        start_superseded_links = {
            item.artifact_id: item.superseded_by_artifact_id
            for item in self.state.planning_artifact_history
        }
        start_active_plan_id = self.state.active_planning_artifact_id
        start_advisor_count = len(self.state.advisor_review_history)
        self.state.messages.append(
            {
                "role": "user",
                "content": [{"type": "text", "text": f"/ultraplan {goal_text}"}],
            }
        )
        if sink is not None:
            sink(
                RuntimeEvent(
                    kind="assistant_tool_call",
                    message=f"ultraplan scout phase launching {len(scout_definitions)} read-only sub-agent(s)",
                )
            )

        scout_results, task_ids = self._run_ultraplan_scouts(goal_text, scout_definitions)
        synthesis_prompt = _build_ultraplan_synthesis_prompt(
            goal_text,
            scout_results,
            previous_plan=parent_artifact,
        )
        try:
            self.run_plugin_hooks("before_plan", goal=goal_text, scout_categories=list(scout_categories))
            response, _ = _create_provider_message_with_retries(
                self.provider,
                session=self,
                messages=[{"role": "user", "content": [{"type": "text", "text": synthesis_prompt}]}],
                tools=[],
                system_prompt=self.build_system_prompt(),
                sink=sink or (lambda _event: None),
                allow_streaming=False,
            )
            final_text = response.text.strip()
            if not final_text:
                raise RuntimeError("Ultraplan synthesis returned an empty response.")
            advisor_review = None
            if self.uses_interactive_advisor():
                advisor_review = _request_advisor_review(
                    self,
                    checkpoint="ultraplan_synthesis",
                    user_prompt=goal_text,
                    candidate_text=final_text,
                    pending_tool_names=(),
                    sink=sink or (lambda _event: None),
                )
                if advisor_review is not None:
                    final_text = _prepend_advisor_review_to_plan(final_text, advisor_review)
            self.state.messages.append(
                {"role": "assistant", "content": [{"type": "text", "text": final_text}]}
            )
            self.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal=goal_text,
                    summary=_summarize_planning_artifact(final_text),
                    supersedes_artifact_id=parent_artifact.artifact_id if parent_artifact is not None else None,
                    derived_from_drift=derived_from_drift,
                    derivation_reason=derivation_reason or "",
                    used_read_only_subagents=True,
                    scout_categories=[item.category for item in scout_definitions],
                    task_ids=task_ids,
                    advisor_status=advisor_review.status if advisor_review is not None else None,
                    advisor_reason=advisor_review.reason if advisor_review is not None else "",
                    advisor_suggested_changes=(
                        list(advisor_review.suggested_changes) if advisor_review is not None else []
                    ),
                    advisor_risk_flags=list(advisor_review.risk_flags) if advisor_review is not None else [],
                )
            )
            self.run_plugin_hooks(
                "after_plan",
                goal=goal_text,
                summary=final_text,
                advisor_status=advisor_review.status if advisor_review is not None else None,
            )
            self.persist_state()
            return final_text
        except Exception:
            del self.state.messages[start_message_count:]
            self.state.context_summary = start_context_summary
            self.state.planning_artifact_history = self.state.planning_artifact_history[:start_planning_count]
            for item in self.state.planning_artifact_history:
                item.superseded_by_artifact_id = start_superseded_links.get(item.artifact_id)
            self.state.recent_planning_artifacts = list(self.state.planning_artifact_history)
            self.state.active_planning_artifact_id = start_active_plan_id
            self.state.advisor_review_history = self.state.advisor_review_history[:start_advisor_count]
            self.state.advisor_last_result = (
                self.state.advisor_review_history[-1] if self.state.advisor_review_history else None
            )
            raise

    def enable_skill(self, name: str) -> str:
        skill_name = name.strip()
        if not skill_name:
            return "Usage: /skills-enable <skill-name>"
        skill = next((skill for skill in self.project_context.skills if skill.name == skill_name), None)
        if skill is None:
            return f'Unknown skill "{skill_name}".'
        if skill_name in self.state.disabled_skill_names:
            self.state.disabled_skill_names = [
                item for item in self.state.disabled_skill_names if item != skill_name
            ]
        if skill_name not in self.state.enabled_skill_names:
            self.state.enabled_skill_names.append(skill_name)
        self.persist_state()
        return f'Enabled skill "{skill_name}".'

    def disable_skill(self, name: str) -> str:
        skill_name = name.strip()
        if not skill_name:
            return "Usage: /skills-disable <skill-name>"
        if skill_name not in {skill.name for skill in self.project_context.skills}:
            return f'Unknown skill "{skill_name}".'
        self.state.enabled_skill_names = [
            item for item in self.state.enabled_skill_names if item != skill_name
        ]
        if skill_name not in self.state.disabled_skill_names:
            self.state.disabled_skill_names.append(skill_name)
        self.persist_state()
        return f'Disabled skill "{skill_name}".'

    def _reconcile_skill_state(self, *, initial_load: bool = False) -> None:
        known_skill_names = {skill.name for skill in self.project_context.skills}
        self.state.enabled_skill_names = [
            name for name in self.state.enabled_skill_names if name in known_skill_names
        ]
        self.state.disabled_skill_names = [
            name for name in self.state.disabled_skill_names if name in known_skill_names
        ]
        if initial_load:
            return

    def _reconcile_plugin_state(self, *, initial_load: bool = False) -> None:
        known_plugin_names = self.plugin_registry.known_plugin_names()
        self.state.enabled_plugin_names = [
            name for name in self.state.enabled_plugin_names if name in known_plugin_names
        ]
        self.state.disabled_plugin_names = [
            name for name in self.state.disabled_plugin_names if name in known_plugin_names
        ]
        if initial_load:
            return

    def _refresh_command_registry(self) -> None:
        self._runtime_context.refresh_command_registry(self.state)

    def _refresh_plugin_runtime(self) -> None:
        self._runtime_context.reload_project_context(self.state)
        self._reconcile_skill_state()
        self._refresh_command_registry()
        if self._session_factory.load_mcp_from_config:
            self._runtime_context.replace_mcp_registry(
                self._session_factory.load_mcp_registry_from_config(
                    self.config,
                    state=self.state,
                )
            )

    def is_bash_command_allowed(self, command: str) -> bool:
        if not self._active_bash_command_prefixes:
            return True
        allowed_prefixes = tuple(prefix.lower() for prefix in self._active_bash_command_prefixes)
        for segment in self._shell_segments(command):
            normalized = segment.lower()
            if any(normalized.startswith(prefix) for prefix in allowed_prefixes):
                continue
            return False
        return True

    def _available_tools(self):
        if self._active_tool_names is None:
            return self.tools
        return [tool for tool in self.tools if tool.name in self._active_tool_names]

    def _build_active_orchestrator(self):
        if self._active_tool_names is None:
            return self.orchestrator
        from .runtime.orchestrator import ToolOrchestrator

        return ToolOrchestrator(list(self._available_tools()))

    def _shell_segments(self, command: str) -> list[str]:
        normalized = command.replace("\r\n", "\n").replace("\n", ";")
        return [
            segment.strip()
            for segment in re.split(r"\s*(?:&&|\|\||;|\|)\s*", normalized)
            if segment.strip()
        ]

    @contextmanager
    def _command_execution_scope(
        self,
        *,
        allowed_tool_names: tuple[str, ...] | None,
        allowed_bash_command_prefixes: tuple[str, ...] | None,
        require_read_only_subagents: bool,
    ):
        previous_tool_names = self._active_tool_names
        previous_bash_prefixes = self._active_bash_command_prefixes
        previous_read_only_subagents = self._require_read_only_subagents
        if allowed_tool_names is not None:
            self._active_tool_names = frozenset(allowed_tool_names)
        if allowed_bash_command_prefixes is not None:
            self._active_bash_command_prefixes = tuple(allowed_bash_command_prefixes)
        if require_read_only_subagents:
            self._require_read_only_subagents = True
        try:
            yield
        finally:
            self._active_tool_names = previous_tool_names
            self._active_bash_command_prefixes = previous_bash_prefixes
            self._require_read_only_subagents = previous_read_only_subagents

    def reload_mcp_from_config(self) -> str:
        new_registry = self._session_factory.load_mcp_registry_from_config(
            self.config,
            state=self.state,
        )
        self._runtime_context.replace_mcp_registry(new_registry)
        self.persist_state()
        if self.mcp_registry is None:
            return "Reloaded MCP configuration. No servers configured."
        failed = sum(
            1
            for server_name in self.mcp_registry.list_servers()
            if self.mcp_registry.get_server(server_name).status == "failed"
        )
        return (
            "Reloaded MCP configuration. "
            f"servers={len(self.mcp_registry.list_servers())} "
            f"tools={len(self.mcp_registry.list_tool_references())} "
            f"failed={failed}"
        )

    def reconnect_mcp_server(self, name: str) -> str:
        server_name = name.strip()
        if not server_name:
            return "Usage: /mcp-reconnect <server-name>"
        if self.mcp_registry is None or server_name not in self.mcp_registry.list_servers():
            return f'Unknown MCP server "{server_name}".'
        server = self.mcp_registry.reconnect_server(server_name)
        self._runtime_context.replace_mcp_registry(self.mcp_registry)
        self.persist_state()
        if server.status == "failed":
            return f'Reconnect failed for "{server_name}": {server.last_error}'
        return (
            f'Reconnected MCP server "{server_name}". '
            f"tools={len(server.tools)} version="
            f"{server.initialize_result.server_version if server.initialize_result is not None else 'unknown'}"
        )

    def handle_mcp_server_failure(self, server_name: str, error_text: str) -> None:
        if self.mcp_registry is None or server_name not in self.mcp_registry.list_servers():
            return
        self.mcp_registry.mark_server_failed(server_name, error_text)

    def ensure_mcp_server_connected(self, server_name: str):
        if self.mcp_registry is None or server_name not in self.mcp_registry.list_servers():
            return None
        return self.mcp_registry.ensure_server_connected(server_name)

    def close(self) -> None:
        try:
            self._runtime_context.close()
        finally:
            if self._workspace_cleanup is not None:
                try:
                    self._workspace_cleanup()
                finally:
                    self._workspace_cleanup = None

    def run_plugin_hooks(self, hook_name: str, **payload: Any) -> list[str]:
        del payload
        return self.plugin_registry.enabled_hook_plugin_names(self.state, hook_name)

    def validate_provider_capabilities(self) -> None:
        capabilities = getattr(self.provider, "capabilities", None)
        if capabilities is None:
            raise ProviderCapabilityError("Provider does not declare runtime capabilities.")
        if self.tool_specs() and not capabilities.supports_tool_calling:
            raise ProviderCapabilityError(
                f'Provider "{capabilities.provider}" with model "{capabilities.model}" '
                "does not support tool calling, but this coding session requires tools."
            )

    def _summarize_message(self, message: dict[str, Any]) -> str:
        content = message.get("content", [])
        if not content:
            return "(empty)"

        text_parts: list[str] = []
        tool_uses: list[str] = []
        tool_results = 0
        for block in content:
            block_type = block.get("type")
            if block_type == "text":
                text = (block.get("text") or "").strip()
                if text:
                    text_parts.append(text)
            elif block_type == "tool_use":
                tool_uses.append(block.get("name", "unknown"))
            elif block_type == "tool_result":
                tool_results += 1

        parts: list[str] = []
        if text_parts:
            text = " ".join(text_parts)
            if len(text) > 100:
                text = text[:97] + "..."
            parts.append(text)
        if tool_uses:
            parts.append("tool_use=" + ",".join(tool_uses))
        if tool_results:
            suffix = "s" if tool_results != 1 else ""
            parts.append(f"tool_result={tool_results} block{suffix}")
        return " | ".join(parts) if parts else "(non-text message)"

    def _compact_multiline_text(self, text: str, *, max_lines: int, max_chars: int) -> str:
        stripped = text.strip()
        if len(stripped) > max_chars:
            stripped = stripped[: max_chars - 3] + "..."
        lines = stripped.splitlines()
        if len(lines) <= max_lines:
            return "\n".join(lines)
        return "\n".join([*lines[:max_lines], "..."])

    def _normalize_advisor_state(self) -> None:
        if self.state.advisor_mode not in ADVISOR_MODES:
            self.state.advisor_mode = "final-review" if self.state.advisor_model else "off"
        if self.state.advisor_model is None:
            self.state.advisor_mode = "off"
        elif self.state.advisor_mode == "off":
            self.state.advisor_mode = "final-review"
        if self.state.active_execution_constraint not in {"normal", "read-only"}:
            self.state.active_execution_constraint = "normal"
        if self.state.active_execution_constraint == "normal":
            self.state.constraint_source = None
            self.state.constraint_reason = None
        if self.state.plan_execution_count < 0:
            self.state.plan_execution_count = 0
        if self.state.plan_drift_count < 0:
            self.state.plan_drift_count = 0
        if len(self.state.advisor_review_history) > MAX_ADVISOR_HISTORY:
            self.state.advisor_review_history = self.state.advisor_review_history[-MAX_ADVISOR_HISTORY:]
        if self.state.advisor_last_result is None and self.state.advisor_review_history:
            self.state.advisor_last_result = self.state.advisor_review_history[-1]
        if self.state.last_plan_drift_status not in {None, "revise", "block"}:
            self.state.last_plan_drift_status = None
        if self.state.last_plan_drift_context is not None and not isinstance(
            self.state.last_plan_drift_context, str
        ):
            self.state.last_plan_drift_context = str(self.state.last_plan_drift_context)
        if self.state.active_execution_plan_id and not isinstance(self.state.active_execution_plan_id, str):
            self.state.active_execution_plan_id = str(self.state.active_execution_plan_id)
        planning_artifacts = (
            list(self.state.planning_artifact_history)
            if self.state.planning_artifact_history
            else list(self.state.recent_planning_artifacts)
        )
        if len(planning_artifacts) > MAX_PLANNING_ARTIFACTS:
            planning_artifacts = planning_artifacts[-MAX_PLANNING_ARTIFACTS:]
        valid_ids = {item.artifact_id for item in planning_artifacts}
        for artifact in planning_artifacts:
            if artifact.supersedes_artifact_id not in valid_ids:
                artifact.supersedes_artifact_id = None
            if artifact.superseded_by_artifact_id not in valid_ids:
                artifact.superseded_by_artifact_id = None
            if not isinstance(artifact.derived_from_drift, bool):
                artifact.derived_from_drift = bool(artifact.derived_from_drift)
            if not isinstance(artifact.derivation_reason, str):
                artifact.derivation_reason = str(artifact.derivation_reason or "")
        self.state.planning_artifact_history = list(planning_artifacts)
        self.state.recent_planning_artifacts = list(planning_artifacts)
        if self.state.active_planning_artifact_id and not any(
            item.artifact_id == self.state.active_planning_artifact_id
            for item in planning_artifacts
        ):
            self.state.active_planning_artifact_id = planning_artifacts[-1].artifact_id if planning_artifacts else None
        elif self.state.active_planning_artifact_id is None and planning_artifacts:
            self.state.active_planning_artifact_id = planning_artifacts[-1].artifact_id

    def _recent_plan_drift_summary(self) -> str | None:
        context = self.state.last_plan_drift_context
        if not context:
            return None
        for line in context.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.endswith(":"):
                continue
            if len(stripped) > 100:
                return stripped[:97] + "..."
            return stripped
        return None

    def _normalize_workspace_state(self) -> None:
        effective_cwd = str(self.config.cwd)
        original_cwd = str(self.config.transcript_cwd or self.config.cwd)
        if self.state.effective_cwd is None:
            self.state.effective_cwd = effective_cwd
        if self.state.original_cwd is None:
            self.state.original_cwd = original_cwd
        if self.state.workspace_mode not in {"main", "snapshot", "worktree"}:
            self.state.workspace_mode = "main"
        if self.state.original_cwd == self.state.effective_cwd:
            self.state.workspace_mode = "main"

    def _run_ultraplan_scouts(
        self,
        goal: str,
        scout_definitions: tuple["_UltraplanScoutDefinition", ...],
    ) -> tuple[dict[str, str], list[str]]:
        results: dict[str, str] = {}
        task_ids: list[str] = []
        tasks = []
        for definition in scout_definitions:
            task = self.task_manager.create(
                "ultraplan_scout",
                definition.description,
                parent_session_id=self.state.session_id,
                provider=self.config.provider,
                model=self.config.model,
                cwd=str(self.config.cwd),
                planner_kind="ultraplan",
                task_role="scout",
                scout_category=definition.category,
                read_only=True,
            )
            self.task_manager.set_progress(task.id, "Queued read-only scout")
            task_ids.append(task.id)
            tasks.append((task.id, definition))

            def worker(task_id=task.id, scout_definition=definition) -> None:
                try:
                    child = self.create_child_session(interactive=False, isolated_workspace=True)
                    self.task_manager.set_progress(
                        task_id,
                        "Running read-only scout",
                        scout_category=scout_definition.category,
                        child_cwd=str(child.config.cwd),
                        workspace_mode=child.state.workspace_mode,
                    )
                    output = child.ask(
                        _build_read_only_subagent_prompt(
                            description=scout_definition.description,
                            prompt=scout_definition.prompt,
                        ),
                        sink=self._build_background_task_sink(task_id),
                        allowed_tool_names=READ_ONLY_SUBAGENT_TOOL_NAMES,
                        allowed_bash_command_prefixes=READ_ONLY_SUBAGENT_BASH_PREFIXES,
                        require_read_only_subagents=True,
                    )
                    self.task_manager.complete(task_id, output)
                except Exception as exc:  # noqa: BLE001
                    self.task_manager.fail(task_id, f"{type(exc).__name__}: {exc}")

            Thread(target=worker, daemon=True).start()

        for task_id, definition in tasks:
            task = self.task_manager.wait_for_task(task_id, timeout_sec=max(self.config.max_turns * 10, 30))
            if task.status == "completed":
                results[definition.category] = task.output.strip() or "(empty scout result)"
            else:
                detail = task.error or task.output or task.progress_summary or "Scout failed."
                results[definition.category] = f"[{task.status}] {detail}"
        return results, task_ids

    def _build_background_task_sink(self, task_id: str):
        def sink(event: RuntimeEvent) -> None:
            summary = self._summarize_runtime_event(event)
            if summary:
                self.task_manager.set_progress(task_id, summary)
                self.task_manager.append_output(task_id, summary + "\n")

        return sink

    def _summarize_runtime_event(self, event: RuntimeEvent) -> str:
        if event.kind == "assistant_text":
            text = event.message.strip()
            if len(text) > 240:
                text = text[:237] + "..."
            return f"[assistant] {text}"
        if event.kind == "plan_execution":
            return f"[plan] {event.message}"
        if event.kind in {
            "advisor",
            "advisor_review_started",
            "advisor_review_result",
            "advisor_revision_requested",
            "advisor_error",
        }:
            return f"[advisor] {event.message}"
        if event.kind == "assistant_tool_call":
            return f"[assistant->tools] {event.message}"
        if event.kind == "assistant_tool_result_ready":
            return f"[tools->assistant] {event.message}"
        if event.kind == "tool_started":
            tool_name = event.tool_name or "unknown"
            return f"[tool:start] {tool_name} {event.message}"
        if event.kind == "tool_finished":
            tool_name = event.tool_name or "unknown"
            suffix = f" ({event.duration_ms}ms)" if event.duration_ms is not None else ""
            return f"[tool:ok] {tool_name}{suffix}"
        if event.kind == "tool_failed":
            tool_name = event.tool_name or "unknown"
            suffix = f" ({event.duration_ms}ms)" if event.duration_ms is not None else ""
            return f"[tool:error] {tool_name}{suffix}: {event.message}"
        if event.kind == "provider_retry":
            return f"[provider:retry] {event.message}"
        if event.kind == "context_compacted":
            return f"[context] {event.message}"
        if event.kind == "tool_result":
            status = "error" if event.is_error else "ok"
            return f"[tool:result] {status}"
        return ""

    def _describe_file_change(self, file_change: WorkspaceFileChange) -> str:
        if not file_change.existed_before and file_change.after_content is not None:
            return f"created {file_change.path}"
        if file_change.existed_before and file_change.after_content is None:
            return f"deleted {file_change.path}"
        return f"updated {file_change.path}"

    def _render_file_change_summary(self, file_change: WorkspaceFileChange) -> str:
        rendered = render_change_summary(
            file_change.path,
            file_change.before_content,
            file_change.after_content,
            max_preview_lines=3,
        )
        return rendered.replace("\n", "\n     ")

    def _render_file_change_detail(self, file_change: WorkspaceFileChange) -> str:
        rendered = render_change_detail(
            file_change.path,
            file_change.before_content,
            file_change.after_content,
            max_lines=12,
        )
        return rendered.replace("\n", "\n     ")

    def _render_change_entry(self, change: WorkspaceChangeSet) -> str:
        summary = change.summary.strip()
        if len(summary) > 52:
            summary = summary[:49] + "..."
        counts = self._count_change_actions(change)
        parts: list[str] = []
        if counts["create"]:
            parts.append(f"c{counts['create']}")
        if counts["update"]:
            parts.append(f"u{counts['update']}")
        if counts["delete"]:
            parts.append(f"d{counts['delete']}")
        action_summary = " ".join(parts) if parts else f"{len(change.files)}f"
        return (
            f"{change.change_id[:8]}  [{change.tool_name}] "
            f"{summary} ({action_summary})"
        )

    def _count_change_actions(self, change: WorkspaceChangeSet) -> dict[str, int]:
        counts = {"create": 0, "update": 0, "delete": 0}
        for file_change in change.files:
            if not file_change.existed_before and file_change.after_content is not None:
                counts["create"] += 1
            elif file_change.existed_before and file_change.after_content is None:
                counts["delete"] += 1
            else:
                counts["update"] += 1
        return counts

    def _apply_change_set(self, change: WorkspaceChangeSet, *, direction: str) -> None:
        file_changes = reversed(change.files) if direction == "undo" else change.files
        for file_change in file_changes:
            path = resolve_workspace_path(self.config.cwd, file_change.path)
            target_exists = (
                file_change.existed_before if direction == "undo" else file_change.after_content is not None
            )
            target_content = (
                file_change.before_content if direction == "undo" else file_change.after_content
            )
            if target_exists:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(target_content or "", encoding="utf-8")
            elif path.exists():
                path.unlink()

    def _summarize_change_operation(self, verb: str, changes: list[WorkspaceChangeSet]) -> str:
        if not changes:
            return f"{verb} 0 change(s)."
        lines = [f"{verb} {len(changes)} change(s)."]
        for change in changes[:3]:
            lines.append(f"- {change.summary} [{change.change_id[:8]}]")
        if len(changes) > 3:
            lines.append(f"- ... {len(changes) - 3} more change(s)")
        return "\n".join(lines)

    def _advisor_context_lines(self, *, exclude_latest_assistant: bool) -> list[str]:
        lines: list[str] = []
        if self.state.context_summary:
            summary = self.state.context_summary.strip()
            if len(summary) > 1200:
                summary = summary[:1197] + "..."
            lines.append("context_summary: " + summary.replace("\n", " | "))
        messages = self.state.messages[:-1] if exclude_latest_assistant and self.state.messages else self.state.messages
        for message in messages[-8:]:
            role = str(message.get("role", "unknown"))
            lines.append(f"{role}: {self._summarize_message(message)}")
        return lines

    def _parse_change_step_count(self, args: str, *, command: str) -> int | str:
        raw = args.strip()
        if not raw:
            return 1
        try:
            count = int(raw)
        except ValueError:
            return f"Usage: {command} [count]"
        if count <= 0:
            return f"Usage: {command} [count]"
        return count

    def _select_changes(
        self,
        stack: list[WorkspaceChangeSet],
        *,
        args: str,
        command: str,
    ) -> list[WorkspaceChangeSet] | str:
        raw = args.strip()
        if not raw:
            return [stack[-1]]
        matches = [change for change in stack if change.change_id.startswith(raw)]
        if matches:
            if len(matches) > 1:
                ids = ", ".join(change.change_id[:8] for change in matches[:5])
                return f'Ambiguous change id "{raw}". Matches: {ids}'
            return matches
        try:
            count = self._parse_change_step_count(raw, command=command)
            if isinstance(count, str):
                raise ValueError
            return list(reversed(stack[-min(count, len(stack)) :]))
        except ValueError:
            return f'Unknown change id "{raw}". Use /changes to inspect the stack.'

    def _mcp_next_steps(
        self,
        source: str,
        *,
        server_name: str,
        tool_name: str,
        model_verification: bool = False,
    ) -> list[str]:
        if source == "ok":
            if model_verification:
                return [
                    "Model chain succeeded. This confirms the model, MCP server, and transport worked together.",
                    f'Use /mcp-call {server_name} {tool_name} ... if you want to inspect the raw MCP tool result separately.',
                ]
            return [
                "Direct MCP call succeeded.",
                f'Use /mcp-verify {server_name} {tool_name} ... to verify the model can invoke the same tool through the normal agent loop.',
            ]
        if source == "config":
            return [
                "Check .pyclaude/mcp_servers.json and confirm the server name is configured correctly.",
                f'Run /mcp and /mcp-tools to confirm "{server_name}" and "{tool_name}" are visible to this session.',
            ]
        if source == "transport":
            return [
                "Check the MCP server URL/process/auth settings and whether the server is reachable.",
                f'Run /mcp or /mcp-reconnect {server_name} to inspect health, retry_in, and reconnect state.',
            ]
        if source == "mcp_server":
            return [
                f'Confirm "{tool_name}" is actually exposed by "{server_name}".',
                f'Run /mcp-tools or /mcp-refresh to refresh the advertised tool list for "{server_name}".',
            ]
        if source == "mcp_tool":
            return [
                "The server was reachable, but the tool itself returned an error payload.",
                "Verify the input arguments and try a direct /mcp-call first before re-running model verification.",
            ]
        if source == "model":
            return [
                f'Run /mcp-call {server_name} {tool_name} ... first to confirm MCP itself works outside the model loop.',
                "If direct MCP succeeds, treat this as a model/tool-calling issue: switch to a stronger tool-calling model or tighten the prompt.",
            ]
        return []

    def _mcp_health_summary(self) -> str:
        if self.mcp_registry is None:
            return "summary: connected=0 failed=0 retrying=0"
        connected = 0
        failed = 0
        retrying = 0
        for server_name in self.mcp_registry.list_servers():
            server = self.mcp_registry.get_server(server_name)
            if server.status == "connected":
                connected += 1
            if server.status == "failed":
                failed += 1
                retrying += 1
            elif self.mcp_registry.retry_wait_seconds(server_name):
                retrying += 1
        return f"summary: connected={connected} failed={failed} retrying={retrying}"


def _render_mcp_content(content: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in content:
        if item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        else:
            parts.append(str(item))
    rendered = "\n".join(part for part in parts if part).strip()
    return rendered or "(empty MCP result)"


def _build_read_only_subagent_prompt(*, description: str, prompt: str) -> str:
    return (
        "You are a read-only planning sub-agent.\n"
        "Do not modify files, do not create commits, and do not run commands with side effects.\n"
        "Use only inspection tools and read-only shell commands. Return findings, constraints, risks, and concrete recommendations.\n\n"
        f"Subtask: {description}\n\n"
        f"{prompt}"
    )


def _build_ultraplan_scout_definitions(
    goal: str,
    categories: tuple[str, ...],
) -> tuple[_UltraplanScoutDefinition, ...]:
    prompts = {
        "architecture-boundaries": (
            "Map the modules, ownership boundaries, and extension points relevant to this goal. "
            "Call out coupling and places where edits are likely required."
        ),
        "data-flow-interfaces": (
            "Trace the data flow, runtime entrypoints, and key interfaces involved in this goal. "
            "Highlight request/response shapes, state transitions, and protocol boundaries."
        ),
        "tests-regressions": (
            "Identify the test surface, likely regressions, and the smallest useful verification plan. "
            "Call out missing tests and brittle areas."
        ),
        "risks-unknowns": (
            "Focus on constraints, unsafe assumptions, migration risks, and unknowns that could block implementation. "
            "Highlight approval, persistence, and compatibility risks when relevant."
        ),
    }
    definitions: list[_UltraplanScoutDefinition] = []
    for category in categories:
        prompt = prompts.get(category)
        if prompt is None:
            continue
        description = category.replace("-", " ")
        definitions.append(
            _UltraplanScoutDefinition(
                category=category,
                description=f"Scout {description} for goal: {goal}",
                prompt=(
                    f"Goal: {goal}\n\n"
                    f"{prompt}\n\n"
                    "Return a concise report with observations, impacted files/modules, risks, and concrete recommendations."
                ),
            )
        )
    return tuple(definitions)


def _build_ultraplan_synthesis_prompt(
    goal: str,
    scout_results: dict[str, str],
    *,
    previous_plan: PlanningArtifact | None = None,
) -> str:
    sections = [
        "You are synthesizing a multi-scout implementation plan.",
        "Use the scout reports below to produce one integrated plan.",
        "Return markdown with exactly these top-level sections:",
        "1. Current Architecture",
        "2. Implementation Plan",
        "3. Risks / Open Questions",
        "4. Verification Checklist",
        "",
        f"Goal: {goal}",
        "",
    ]
    if previous_plan is not None:
        sections.extend(
            [
                "Previous active plan to revise:",
                f"- artifact_id: {previous_plan.artifact_id}",
                f"- goal: {previous_plan.goal}",
                "- summary:",
                previous_plan.summary.strip() or "(empty prior summary)",
                "",
            ]
        )
    sections.append("Scout reports:")
    for category, content in scout_results.items():
        sections.extend(
            [
                f"## {category}",
                content.strip() or "(empty scout result)",
                "",
            ]
        )
    sections.extend(
        [
            "Requirements:",
            "- Ground the plan in the scout findings instead of ignoring them.",
            "- If a previous active plan is provided, explicitly preserve its valid parts and replace weak ones.",
            "- Name concrete modules/files when possible.",
            "- Keep the plan actionable and ordered.",
            "- Verification Checklist should be a flat checklist.",
        ]
    )
    return "\n".join(sections)


def _summarize_planning_artifact(text: str, *, max_chars: int = 600) -> str:
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    return stripped[: max_chars - 3] + "..."


def _prepend_advisor_review_to_plan(text: str, review: AdvisorReviewSummary) -> str:
    lines = [
        "Advisor Review",
        f"- status: {review.status}",
    ]
    if review.reason:
        lines.append(f"- reason: {review.reason}")
    if review.risk_flags:
        lines.append("- risk_flags: " + ", ".join(review.risk_flags))
    if review.suggested_changes:
        lines.append("- suggested_changes:")
        lines.extend(f"  - {item}" for item in review.suggested_changes)
    return "\n".join(lines) + "\n\n" + text.strip()
