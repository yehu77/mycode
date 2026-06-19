from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .agents import (
    AgentDefinitionRegistry,
    build_builtin_agent_registry,
    load_project_local_agent_registry,
    merge_agent_registries,
)
from .config import SessionConfig
from .mcp import McpRegistry, default_mcp_config_path, load_mcp_registry_from_payloads, load_mcp_server_payloads
from .plugins import (
    PluginRegistry,
    build_builtin_plugin_registry,
    load_project_local_plugin_registry,
    merge_plugin_registries,
)
from .runtime.context import SessionRuntimeContext, build_session_runtime_context
from .state import HistoryBoundary, SessionState
from .storage.transcript import load_latest_transcript, load_transcript_by_session_id
from .tasks import TaskManager
from .workspace import cleanup_isolated_workspace, prepare_isolated_workspace
from .workspace.isolation import derive_workspace_health

_UNSET = object()


class SessionFactory:
    def __init__(
        self,
        *,
        load_mcp_from_config: bool = False,
        plugin_registry: PluginRegistry | None = None,
        agent_registry: AgentDefinitionRegistry | None = None,
    ) -> None:
        self.load_mcp_from_config = load_mcp_from_config
        self.plugin_registry = plugin_registry or build_builtin_plugin_registry()
        self.agent_registry = agent_registry or build_builtin_agent_registry()

    def resolve_plugin_registry(self, cwd: Path) -> PluginRegistry:
        return merge_plugin_registries(
            self.plugin_registry,
            load_project_local_plugin_registry(cwd),
        )

    def resolve_agent_registry(self, cwd: Path) -> AgentDefinitionRegistry:
        return merge_agent_registries(
            self.agent_registry,
            load_project_local_agent_registry(cwd),
        )

    def create_runtime_context(
        self,
        config: SessionConfig,
        *,
        state: SessionState | None = None,
        task_manager: TaskManager | None = None,
        mcp_registry: McpRegistry | None | object = _UNSET,
        owns_mcp_registry: bool = True,
    ) -> SessionRuntimeContext:
        resolved_state = state or SessionState()
        resolved_task_manager = task_manager
        if resolved_task_manager is None and resolved_state.saved_task_records:
            resolved_task_manager = TaskManager.from_snapshot(resolved_state.saved_task_records)
        resolved_plugin_registry = self.resolve_plugin_registry(config.cwd)
        resolved_mcp_registry = self._resolve_mcp_registry(
            config,
            resolved_state,
            resolved_plugin_registry,
            mcp_registry,
        )
        return build_session_runtime_context(
            config,
            initial_state=resolved_state,
            task_manager=resolved_task_manager,
            mcp_registry=resolved_mcp_registry,
            owns_mcp_registry=owns_mcp_registry,
            plugin_registry=resolved_plugin_registry,
        )

    def create_session(
        self,
        config: SessionConfig,
        *,
        state: SessionState | None = None,
        task_manager: TaskManager | None = None,
        mcp_registry: McpRegistry | None | object = _UNSET,
        owns_mcp_registry: bool = True,
        depth: int = 0,
        persist_transcript: bool | None = None,
    ):
        from .session import Session

        runtime_context = self.create_runtime_context(
            config,
            state=state,
            task_manager=task_manager,
            mcp_registry=mcp_registry,
            owns_mcp_registry=owns_mcp_registry,
        )
        return Session(
            config,
            state=state,
            depth=depth,
            persist_transcript=persist_transcript,
            runtime_context=runtime_context,
            session_factory=self,
        )

    def create_or_restore_session(
        self,
        config: SessionConfig,
        *,
        restore_latest: bool = False,
        resume_session_id: str | None = None,
        task_manager: TaskManager | None = None,
        mcp_registry: McpRegistry | None | object = _UNSET,
        depth: int = 0,
        persist_transcript: bool | None = None,
    ):
        state, restored_from = self.load_saved_state(
            config.cwd,
            restore_latest=restore_latest,
            resume_session_id=resume_session_id,
        )
        resolved_config = self._apply_restored_workspace_config(config, state)
        session = self.create_session(
            resolved_config,
            state=state,
            task_manager=task_manager,
            mcp_registry=mcp_registry,
            depth=depth,
            persist_transcript=persist_transcript,
        )
        return session, restored_from

    def load_saved_state(
        self,
        cwd: Path,
        *,
        restore_latest: bool = False,
        resume_session_id: str | None = None,
    ) -> tuple[SessionState | None, Path | None]:
        if resume_session_id is not None:
            restored_state, restored_from = load_transcript_by_session_id(cwd, resume_session_id)
            if restored_state is None:
                raise FileNotFoundError(f'No saved session found for id "{resume_session_id}".')
            self._resolve_restored_workspace_state(restored_state, cwd)
            self._mark_plan_mode_reentry_attachment(restored_state)
            self._record_resume_boundary(restored_state)
            return restored_state, restored_from
        if restore_latest:
            restored_state, restored_from = load_latest_transcript(cwd)
            if restored_state is not None:
                self._resolve_restored_workspace_state(restored_state, cwd)
                self._mark_plan_mode_reentry_attachment(restored_state)
                self._record_resume_boundary(restored_state)
            return restored_state, restored_from
        return None, None

    def _record_resume_boundary(self, state: SessionState) -> None:
        state.history_boundaries.append(
            HistoryBoundary(
                kind="resume",
                trigger="saved_resume",
                summary="Restored saved session state from transcript.",
                message_count_before=len(state.messages),
                message_count_after=len(state.messages),
                context_summary_chars_before=len(state.context_summary or ""),
                context_summary_chars_after=len(state.context_summary or ""),
                new_session_id=state.session_id,
            )
        )

    def create_child_session(
        self,
        parent,
        *,
        interactive: bool = False,
        isolated_workspace: bool = False,
    ):
        child_cwd = parent.config.cwd
        child_mcp_config = parent.config.mcp_config_path
        child_permission_config = parent.config.permission_config_path
        child_transcript_cwd = parent.config.transcript_cwd
        workspace = None
        if isolated_workspace:
            workspace = prepare_isolated_workspace(parent.config.cwd, label="agent")
            child_cwd = workspace.effective_cwd
            child_mcp_config = self._map_child_mcp_config(parent.config, child_cwd)
            child_permission_config = self._map_child_permission_config(parent.config, child_cwd)
            child_transcript_cwd = parent.config.transcript_cwd or parent.config.cwd
        child_config = replace(
            parent.config,
            interactive=interactive,
            cwd=child_cwd,
            transcript_cwd=child_transcript_cwd,
            mcp_config_path=child_mcp_config,
            permission_config_path=child_permission_config,
        )
        child_mcp_registry, owns_child_mcp_registry = self._resolve_child_mcp_registry(
            parent,
            child_config,
            isolated_workspace=isolated_workspace,
        )
        child_state = SessionState(
            session_runtime_mode=parent.state.session_runtime_mode,
            pre_plan_mode=parent.state.pre_plan_mode,
            has_exited_plan_mode=parent.state.has_exited_plan_mode,
            needs_plan_mode_exit_attachment=parent.state.needs_plan_mode_exit_attachment,
            needs_plan_mode_reentry_attachment=parent.state.needs_plan_mode_reentry_attachment,
            session_execution_mode="child-session",
            session_command_policy_name=parent.state.session_command_policy_name,
            session_command_policy_source=parent.state.session_command_policy_source,
            session_command_policy_allowed_tool_names=list(
                parent.state.session_command_policy_allowed_tool_names
            ),
            session_command_policy_allowed_bash_prefixes=list(
                parent.state.session_command_policy_allowed_bash_prefixes
            ),
            session_command_policy_require_read_only_subagents=(
                parent.state.session_command_policy_require_read_only_subagents
            ),
            active_execution_constraint=(
                "read-only"
                if parent.state.session_command_policy_require_read_only_subagents
                or parent.state.active_execution_constraint == "read-only"
                else "normal"
            ),
            constraint_source=(
                parent.state.constraint_source
                if parent.state.session_command_policy_require_read_only_subagents
                or parent.state.active_execution_constraint == "read-only"
                else None
            ),
            constraint_reason=(
                parent.state.constraint_reason
                if parent.state.session_command_policy_require_read_only_subagents
                or parent.state.active_execution_constraint == "read-only"
                else None
            ),
            original_cwd=str((parent.config.transcript_cwd or parent.config.cwd).resolve()),
            effective_cwd=str(child_cwd.resolve()),
            workspace_mode=workspace.mode if workspace is not None else "main",
            workspace_label=workspace.label if workspace is not None else None,
            workspace_created_at=workspace.created_at if workspace is not None else None,
            workspace_health=(
                derive_workspace_health(
                    workspace_mode=workspace.mode,
                    workspace_cleanup_status="pending",
                    workspace_unavailable=False,
                )
                if workspace is not None
                else "healthy"
            ),
            workspace_cleanup_status="pending" if workspace is not None else "none",
        )
        child_session = self.create_session(
            child_config,
            state=child_state,
            task_manager=parent.task_manager,
            mcp_registry=child_mcp_registry,
            owns_mcp_registry=owns_child_mcp_registry,
            depth=parent.depth + 1,
            persist_transcript=False,
        )
        parent.copy_plan_for_fork(child_session)
        if workspace is not None:
            child_session.set_workspace_cleanup(lambda: cleanup_isolated_workspace(workspace))
        return child_session

    def _mark_plan_mode_reentry_attachment(self, state: SessionState) -> None:
        if state.session_runtime_mode != "plan":
            return
        if not state.plan_slug:
            return
        state.needs_plan_mode_reentry_attachment = True

    def load_mcp_registry_from_config(
        self,
        config: SessionConfig,
        *,
        state: SessionState | None = None,
        plugin_registry: PluginRegistry | None = None,
    ) -> McpRegistry | None:
        server_payloads = self._load_server_payloads(config)
        resolved_plugin_registry = plugin_registry or self.resolve_plugin_registry(config.cwd)
        if state is not None:
            server_payloads.extend(resolved_plugin_registry.enabled_mcp_server_payloads(state))
        return load_mcp_registry_from_payloads(config.cwd, server_payloads)

    def _resolve_mcp_registry(
        self,
        config: SessionConfig,
        state: SessionState,
        plugin_registry: PluginRegistry,
        mcp_registry: McpRegistry | None | object,
    ) -> McpRegistry | None:
        if mcp_registry is not _UNSET:
            return mcp_registry
        if not self.load_mcp_from_config:
            return None
        return self.load_mcp_registry_from_config(
            config,
            state=state,
            plugin_registry=plugin_registry,
        )

    def _load_server_payloads(self, config: SessionConfig) -> list[dict[str, object]]:
        path = config.mcp_config_path or default_mcp_config_path(config.cwd)
        if not path.exists():
            return []
        return load_mcp_server_payloads(path)

    def _map_child_mcp_config(self, config: SessionConfig, child_cwd: Path) -> Path | None:
        if config.mcp_config_path is None:
            return None
        try:
            relative = config.mcp_config_path.resolve().relative_to(config.cwd.resolve())
        except ValueError:
            return config.mcp_config_path
        return (child_cwd / relative).resolve()

    def _map_child_permission_config(self, config: SessionConfig, child_cwd: Path) -> Path | None:
        if config.permission_config_path is None:
            return None
        try:
            relative = config.permission_config_path.resolve().relative_to(config.cwd.resolve())
        except ValueError:
            return config.permission_config_path
        return (child_cwd / relative).resolve()

    def _resolve_restored_workspace_state(self, state: SessionState, cwd: Path) -> None:
        if state.workspace_mode not in {"snapshot", "worktree"}:
            state.workspace_unavailable = False
            state.workspace_unavailable_reason = None
            state.workspace_fallback_cwd = None
            state.workspace_health = derive_workspace_health(
                workspace_mode=state.workspace_mode,
                workspace_cleanup_status=state.workspace_cleanup_status,
                workspace_unavailable=False,
            )
            return
        effective_raw = state.effective_cwd or ""
        original_raw = state.original_cwd or str(cwd.resolve())
        try:
            effective_path = Path(effective_raw).resolve() if effective_raw else cwd.resolve()
        except OSError:
            effective_path = cwd.resolve()
        try:
            original_path = Path(original_raw).resolve()
        except OSError:
            original_path = cwd.resolve()
        fallback_path = original_path if original_path.exists() else cwd.resolve()
        state.workspace_fallback_cwd = str(fallback_path)
        if effective_raw and effective_path.exists():
            state.workspace_unavailable = False
            state.workspace_unavailable_reason = None
            state.workspace_health = derive_workspace_health(
                workspace_mode=state.workspace_mode,
                workspace_cleanup_status=state.workspace_cleanup_status,
                workspace_unavailable=False,
            )
            return
        state.workspace_unavailable = True
        state.workspace_unavailable_reason = (
            f"Isolated workspace is unavailable: expected {effective_raw or effective_path}"
        )
        state.workspace_health = derive_workspace_health(
            workspace_mode=state.workspace_mode,
            workspace_cleanup_status=state.workspace_cleanup_status,
            workspace_unavailable=True,
        )

    def _apply_restored_workspace_config(
        self,
        config: SessionConfig,
        state: SessionState | None,
    ) -> SessionConfig:
        if state is None or not state.workspace_unavailable:
            return config
        fallback = Path(state.workspace_fallback_cwd or state.original_cwd or config.cwd).resolve()
        transcript_cwd = Path(state.original_cwd).resolve() if state.original_cwd else (config.transcript_cwd or config.cwd)
        return replace(
            config,
            cwd=fallback,
            transcript_cwd=transcript_cwd,
        )

    def _resolve_child_mcp_registry(
        self,
        parent,
        child_config: SessionConfig,
        *,
        isolated_workspace: bool,
    ) -> tuple[McpRegistry | None | object, bool]:
        if isolated_workspace and child_config.mcp_config_path is not None:
            return self.load_mcp_registry_from_config(child_config, state=parent.state), True
        if self.load_mcp_from_config:
            return self.load_mcp_registry_from_config(child_config, state=parent.state), True
        if parent.mcp_registry is not None:
            return parent.mcp_registry, False
        return None, True
