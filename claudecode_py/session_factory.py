from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .config import SessionConfig
from .mcp import McpRegistry, default_mcp_config_path, load_mcp_registry_from_payloads, load_mcp_server_payloads
from .plugins import PluginRegistry, build_builtin_plugin_registry
from .runtime.context import SessionRuntimeContext, build_session_runtime_context
from .state import SessionState
from .storage.transcript import load_latest_transcript, load_transcript_by_session_id
from .tasks import TaskManager
from .workspace import cleanup_isolated_workspace, prepare_isolated_workspace

_UNSET = object()


class SessionFactory:
    def __init__(
        self,
        *,
        load_mcp_from_config: bool = False,
        plugin_registry: PluginRegistry | None = None,
    ) -> None:
        self.load_mcp_from_config = load_mcp_from_config
        self.plugin_registry = plugin_registry or build_builtin_plugin_registry()

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
        resolved_mcp_registry = self._resolve_mcp_registry(
            config,
            resolved_state,
            mcp_registry,
        )
        return build_session_runtime_context(
            config,
            initial_state=resolved_state,
            task_manager=task_manager,
            mcp_registry=resolved_mcp_registry,
            owns_mcp_registry=owns_mcp_registry,
            plugin_registry=self.plugin_registry,
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
        session = self.create_session(
            config,
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
            return restored_state, restored_from
        if restore_latest:
            return load_latest_transcript(cwd)
        return None, None

    def create_child_session(
        self,
        parent,
        *,
        interactive: bool = False,
        isolated_workspace: bool = False,
    ):
        child_cwd = parent.config.cwd
        child_mcp_config = parent.config.mcp_config_path
        child_transcript_cwd = parent.config.transcript_cwd
        workspace = None
        if isolated_workspace:
            workspace = prepare_isolated_workspace(parent.config.cwd, label="agent")
            child_cwd = workspace.effective_cwd
            child_mcp_config = self._map_child_mcp_config(parent.config, child_cwd)
            child_transcript_cwd = parent.config.transcript_cwd or parent.config.cwd
        child_config = replace(
            parent.config,
            interactive=interactive,
            cwd=child_cwd,
            transcript_cwd=child_transcript_cwd,
            mcp_config_path=child_mcp_config,
        )
        child_mcp_registry, owns_child_mcp_registry = self._resolve_child_mcp_registry(
            parent,
            child_config,
            isolated_workspace=isolated_workspace,
        )
        child_state = SessionState(
            original_cwd=str((parent.config.transcript_cwd or parent.config.cwd).resolve()),
            effective_cwd=str(child_cwd.resolve()),
            workspace_mode=workspace.mode if workspace is not None else "main",
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
        if workspace is not None:
            child_session.set_workspace_cleanup(lambda: cleanup_isolated_workspace(workspace))
        return child_session

    def load_mcp_registry_from_config(
        self,
        config: SessionConfig,
        *,
        state: SessionState | None = None,
    ) -> McpRegistry | None:
        server_payloads = self._load_server_payloads(config)
        if state is not None:
            server_payloads.extend(self.plugin_registry.enabled_mcp_server_payloads(state))
        return load_mcp_registry_from_payloads(config.cwd, server_payloads)

    def _resolve_mcp_registry(
        self,
        config: SessionConfig,
        state: SessionState,
        mcp_registry: McpRegistry | None | object,
    ) -> McpRegistry | None:
        if mcp_registry is not _UNSET:
            return mcp_registry
        if not self.load_mcp_from_config:
            return None
        return self.load_mcp_registry_from_config(config, state=state)

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
