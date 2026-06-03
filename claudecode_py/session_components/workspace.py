from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..permissions import ApprovalRequest
from ..state import WorkspaceFileChange
from ..storage.background_sessions import BackgroundSessionRecord, list_background_sessions, update_background_session
from ..storage.transcript import list_transcripts, update_transcript_workspace_metadata
from ..workspace.isolation import (
    IsolatedWorkspace,
    OrphanedWorkspaceDiagnostic,
    cleanup_isolated_workspace,
    cleanup_orphaned_workspace,
    diagnose_orphaned_workspaces,
    repair_isolated_workspace,
)


def _workspace_action_bundle(
    *,
    workspace_health: str,
    workspace_label: str | None = None,
    session_id: str | None = None,
    workspace_action: str | None = None,
    workspace_target: str | None = None,
) -> dict[str, str]:
    selector = (workspace_target or session_id or workspace_label or "all").strip() or "all"
    if workspace_action == "repair":
        return {
            "primary_action": f"workspace_repair {selector}",
            "secondary_action": "workspace_cleanup_preview",
            "tertiary_action": "/workspaces list",
            "target": selector,
            "workspace_health": workspace_health,
        }
    if workspace_action == "cleanup":
        return {
            "primary_action": "workspace_cleanup_preview",
            "secondary_action": f"workspace_cleanup_apply {selector}",
            "tertiary_action": "/workspaces list",
            "target": selector,
            "workspace_health": workspace_health,
        }
    if workspace_health == "unavailable":
        return {
            "primary_action": f"workspace_repair {selector}",
            "secondary_action": "workspace_cleanup_preview",
            "tertiary_action": "/workspaces list",
            "target": selector,
            "workspace_health": workspace_health,
        }
    if workspace_health == "orphaned":
        cleanup_target = (workspace_label or workspace_target or "all").strip() or "all"
        return {
            "primary_action": "workspace_cleanup_preview",
            "secondary_action": f"workspace_cleanup_apply {cleanup_target}",
            "tertiary_action": "/workspaces list",
            "target": cleanup_target,
            "workspace_health": workspace_health,
        }
    return {
        "primary_action": "none",
        "secondary_action": "none",
        "tertiary_action": "/workspaces list",
        "target": selector,
        "workspace_health": workspace_health,
    }


@dataclass(slots=True)
class _WorkspaceInventoryReference:
    source: str
    label: str
    mode: str
    original_cwd: str
    effective_cwd: str
    workspace_created_at: str | None
    workspace_cleanup_status: str
    workspace_cleanup_error: str | None
    workspace_unavailable: bool
    workspace_unavailable_reason: str | None
    workspace_fallback_cwd: str | None
    workspace_health: str
    session_id: str | None = None
    transcript_path: Path | None = None
    background_record: BackgroundSessionRecord | None = None


class WorkspaceSessionComponent:
    def __init__(self, session: Any) -> None:
        self._session = session

    def _workspace_anomaly_summary(self, item: dict[str, Any]) -> str:
        session = self._session
        return session._workspace_anomaly_summary(
            workspace_health=str(item.get("workspace_health") or "healthy"),
            workspace_cleanup_status=str(item.get("workspace_cleanup_status") or "none"),
            workspace_unavailable=bool(item.get("workspace_unavailable")),
            workspace_unavailable_reason=str(item.get("workspace_unavailable_reason") or "").strip() or None,
            workspace_fallback_cwd=str(item.get("workspace_fallback_cwd") or "").strip() or None,
            workspace_cleanup_error=str(item.get("workspace_cleanup_error") or "").strip() or None,
        )

    def _workspace_selector_for_item(self, item: dict[str, Any]) -> str:
        session_ids = sorted(str(session_id).strip() for session_id in item.get("session_ids", set()) if str(session_id).strip())
        if len(session_ids) == 1:
            return session_ids[0]
        label = str(item.get("label") or "").strip()
        return label or "all"

    def _workspace_action_bundle_for_item(self, item: dict[str, Any]) -> dict[str, str]:
        return _workspace_action_bundle(
            workspace_health=str(item.get("workspace_health") or "healthy"),
            workspace_label=str(item.get("label") or "").strip() or None,
            session_id=(
                self._workspace_selector_for_item(item)
                if len(item.get("session_ids", set()) or []) == 1
                else None
            ),
            workspace_target=self._workspace_selector_for_item(item),
        )

    def _render_workspace_detail_lines(
        self,
        item: dict[str, Any],
        *,
        title: str,
        action_bundle: dict[str, str],
    ) -> list[str]:
        session = self._session
        selector = self._workspace_selector_for_item(item)
        recommended_actions = session._workspace_recommended_actions(
            workspace_health=str(item.get("workspace_health") or "healthy"),
            workspace_label=str(item.get("label") or "").strip() or None,
            session_id=selector if len(item.get("session_ids", set()) or []) == 1 else None,
        )
        anomaly_summary = self._workspace_anomaly_summary(item)
        lines = [
            title,
            "workspace state:",
            f"- label: {item.get('label') or 'none'}",
            f"- mode: {item.get('mode') or 'main'}",
            f"- health: {item.get('workspace_health') or 'healthy'}",
            f"- origin: {item.get('original_cwd') or ''}",
            f"- effective_cwd: {item.get('effective_cwd') or ''}",
            f"- cleanup_status: {item.get('workspace_cleanup_status') or 'none'}",
            f"- session_refs: {int(item.get('session_refs') or 0)}",
            f"- background_refs: {int(item.get('background_refs') or 0)}",
            "workspace anomaly:",
            f"- workspace anomaly: {anomaly_summary}",
            f"- workspace_unavailable_reason: {item.get('workspace_unavailable_reason') or 'none'}",
            f"- workspace_cleanup_error: {item.get('workspace_cleanup_error') or 'none'}",
            f"- workspace_fallback_cwd: {item.get('workspace_fallback_cwd') or 'none'}",
            "workspace recovery:",
            f"- workspace recovery: {recommended_actions[0] if recommended_actions else 'none'}",
            "- workspace_recommended_actions: "
            + (", ".join(recommended_actions) if recommended_actions else "none"),
            f"- selected_workspace_primary_action: {action_bundle.get('primary_action') or 'none'}",
            f"- selected_workspace_secondary_action: {action_bundle.get('secondary_action') or 'none'}",
            f"- selected_workspace_tertiary_action: {action_bundle.get('tertiary_action') or 'none'}",
            f"- selected_workspace_target: {action_bundle.get('target') or 'none'}",
            "next actions:",
            f"- primary action: {action_bundle.get('primary_action') or 'none'}",
            f"- secondary action: {action_bundle.get('secondary_action') or 'none'}",
            f"- tertiary action: {action_bundle.get('tertiary_action') or 'none'}",
            f"label: {item.get('label') or 'none'}",
            f"mode: {item.get('mode') or 'main'}",
            f"health: {item.get('workspace_health') or 'healthy'}",
            f"origin: {item.get('original_cwd') or ''}",
            f"effective_cwd: {item.get('effective_cwd') or ''}",
            f"cleanup_status: {item.get('workspace_cleanup_status') or 'none'}",
            f"session_refs: {int(item.get('session_refs') or 0)}",
            f"background_refs: {int(item.get('background_refs') or 0)}",
            f"primary action: {action_bundle.get('primary_action') or 'none'}",
            f"secondary action: {action_bundle.get('secondary_action') or 'none'}",
            f"tertiary action: {action_bundle.get('tertiary_action') or 'none'}",
        ]
        if item.get("workspace_created_at"):
            lines.insert(9, f"- created_at: {item['workspace_created_at']}")
            lines.append(f"created_at: {item['workspace_created_at']}")
        if item.get("workspace_fallback_cwd"):
            lines.append(f"fallback_cwd: {item['workspace_fallback_cwd']}")
        if item.get("workspace_unavailable_reason"):
            lines.append(f"unavailable_reason: {item['workspace_unavailable_reason']}")
        if item.get("workspace_cleanup_error"):
            lines.append(f"cleanup_error: {item['workspace_cleanup_error']}")
        session_ids = sorted(
            str(session_id).strip()
            for session_id in item.get("session_ids", set())
            if str(session_id).strip()
        )
        if session_ids:
            lines.append("session_ids: " + ", ".join(session_ids))
        background_ids = sorted(
            str(background_id).strip()
            for background_id in item.get("background_ids", set())
            if str(background_id).strip()
        )
        if background_ids:
            lines.append("background_ids: " + ", ".join(background_ids))
        transcript_paths = [
            str(path)
            for path in item.get("transcript_paths", [])
            if str(path).strip()
        ]
        if transcript_paths:
            lines.append("transcript_paths:")
            for path in transcript_paths:
                lines.append("- " + path)
        return lines

    def _workspace_anchor_cwd(self) -> Path:
        session = self._session
        if session.state.original_cwd:
            return Path(session.state.original_cwd)
        if session.config.transcript_cwd is not None:
            return session.config.transcript_cwd
        return session.config.cwd

    def _workspace_reference_paths(self) -> set[Path]:
        anchor = self._workspace_anchor_cwd().resolve()
        references: set[Path] = set()

        def add_reference(raw: str | None) -> None:
            if not raw:
                return
            try:
                resolved = Path(raw).resolve()
            except OSError:
                return
            references.add(resolved)

        session = self._session
        if session.state.workspace_mode in {"snapshot", "worktree"}:
            add_reference(session.state.effective_cwd)

        for item in list_transcripts(anchor, limit=None):
            if (item.workspace_mode or "main") not in {"snapshot", "worktree"}:
                continue
            add_reference(item.effective_cwd or item.cwd)

        for record in list_background_sessions(anchor):
            if record.workspace_mode not in {"snapshot", "worktree"}:
                continue
            if record.status in {"completed", "failed", "stopped"}:
                continue
            add_reference(record.effective_cwd or record.cwd)
        return references

    def orphaned_workspace_diagnostics(self) -> list[OrphanedWorkspaceDiagnostic]:
        return diagnose_orphaned_workspaces(
            self._workspace_anchor_cwd(),
            referenced_paths=self._workspace_reference_paths(),
        )

    def _render_orphaned_workspace_lines(self) -> list[str]:
        orphans = self.orphaned_workspace_diagnostics()
        lines = [f"orphaned_isolated_workspaces: {len(orphans)}"]
        if not orphans:
            return lines
        preview = orphans[:5]
        for item in preview:
            lines.append("- " + self._render_orphaned_workspace_entry(item))
        remaining = len(orphans) - len(preview)
        if remaining > 0:
            lines.append(f"- ... {remaining} more orphaned workspaces")
        return lines

    def _render_orphaned_workspace_entry(self, item: OrphanedWorkspaceDiagnostic) -> str:
        origin = self._workspace_anchor_cwd().resolve()
        return (
            f"workspace={item.mode} health=orphaned label={item.label} "
            f"origin={origin} cwd={item.path} cleanup=none session_refs=0 background_refs=0"
        )

    def _workspace_inventory_references(self) -> list[_WorkspaceInventoryReference]:
        session = self._session
        references: list[_WorkspaceInventoryReference] = []

        def append_reference(
            *,
            source: str,
            session_id: str | None,
            label: str | None,
            mode: str | None,
            original_cwd: str | None,
            effective_cwd: str | None,
            workspace_created_at: str | None,
            workspace_health: str | None,
            workspace_cleanup_status: str | None,
            workspace_cleanup_error: str | None,
            workspace_unavailable: bool,
            workspace_unavailable_reason: str | None,
            workspace_fallback_cwd: str | None,
            transcript_path: Path | None = None,
            background_record: BackgroundSessionRecord | None = None,
        ) -> None:
            normalized_mode = str(mode or "main")
            if normalized_mode not in {"snapshot", "worktree"}:
                return
            normalized_label = str(label or "").strip()
            normalized_original = str(original_cwd or self._workspace_anchor_cwd()).strip()
            normalized_effective = str(effective_cwd or normalized_original).strip()
            cleanup_status = str(workspace_cleanup_status or "none")
            health = str(
                workspace_health
                or session._derive_workspace_health(
                    workspace_mode=normalized_mode,
                    workspace_cleanup_status=cleanup_status,
                    workspace_unavailable=workspace_unavailable,
                )
            )
            references.append(
                _WorkspaceInventoryReference(
                    source=source,
                    label=normalized_label or Path(normalized_effective).name,
                    mode=normalized_mode,
                    original_cwd=normalized_original,
                    effective_cwd=normalized_effective,
                    workspace_created_at=workspace_created_at,
                    workspace_cleanup_status=cleanup_status,
                    workspace_cleanup_error=workspace_cleanup_error,
                    workspace_unavailable=workspace_unavailable,
                    workspace_unavailable_reason=workspace_unavailable_reason,
                    workspace_fallback_cwd=workspace_fallback_cwd,
                    workspace_health=health,
                    session_id=session_id,
                    transcript_path=transcript_path,
                    background_record=background_record,
                )
            )

        append_reference(
            source="current",
            session_id=session.state.session_id,
            label=session.state.workspace_label,
            mode=session.state.workspace_mode,
            original_cwd=session.state.original_cwd,
            effective_cwd=session.state.effective_cwd,
            workspace_created_at=session.state.workspace_created_at,
            workspace_health=session.state.workspace_health,
            workspace_cleanup_status=session.state.workspace_cleanup_status,
            workspace_cleanup_error=session.state.workspace_cleanup_error,
            workspace_unavailable=bool(session.state.workspace_unavailable),
            workspace_unavailable_reason=session.state.workspace_unavailable_reason,
            workspace_fallback_cwd=session.state.workspace_fallback_cwd,
        )
        for item in list_transcripts(self._workspace_anchor_cwd(), limit=None):
            append_reference(
                source="saved",
                session_id=item.session_id,
                label=item.workspace_label,
                mode=item.workspace_mode,
                original_cwd=item.original_cwd,
                effective_cwd=item.effective_cwd or item.cwd,
                workspace_created_at=item.workspace_created_at,
                workspace_health=item.workspace_health,
                workspace_cleanup_status=item.workspace_cleanup_status,
                workspace_cleanup_error=None,
                workspace_unavailable=bool(item.workspace_unavailable),
                workspace_unavailable_reason=item.workspace_unavailable_reason,
                workspace_fallback_cwd=item.workspace_fallback_cwd,
                transcript_path=item.path,
            )
        for record in list_background_sessions(self._workspace_anchor_cwd()):
            append_reference(
                source="background",
                session_id=record.session_id,
                label=record.workspace_label,
                mode=record.workspace_mode,
                original_cwd=record.original_cwd,
                effective_cwd=record.effective_cwd or record.cwd,
                workspace_created_at=record.workspace_created_at,
                workspace_health=record.workspace_health,
                workspace_cleanup_status=record.workspace_cleanup_status,
                workspace_cleanup_error=record.workspace_cleanup_error,
                workspace_unavailable=record.workspace_unavailable,
                workspace_unavailable_reason=record.workspace_unavailable_reason,
                workspace_fallback_cwd=record.workspace_fallback_cwd,
                background_record=record,
            )
        return references

    def _workspace_inventory(self) -> list[dict[str, Any]]:
        session = self._session
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for reference in self._workspace_inventory_references():
            key = (
                str(Path(reference.original_cwd).resolve()),
                reference.label.casefold(),
                reference.mode,
            )
            group = grouped.setdefault(
                key,
                {
                    "label": reference.label,
                    "mode": reference.mode,
                    "original_cwd": reference.original_cwd,
                    "effective_cwd": reference.effective_cwd,
                    "workspace_created_at": reference.workspace_created_at,
                    "workspace_cleanup_status": reference.workspace_cleanup_status,
                    "workspace_cleanup_error": reference.workspace_cleanup_error,
                    "workspace_unavailable": reference.workspace_unavailable,
                    "workspace_unavailable_reason": reference.workspace_unavailable_reason,
                    "workspace_fallback_cwd": reference.workspace_fallback_cwd,
                    "workspace_health": reference.workspace_health,
                    "session_ids": set(),
                    "background_ids": set(),
                    "transcript_paths": [],
                    "has_current": False,
                },
            )
            if reference.source == "current":
                group["has_current"] = True
                group["effective_cwd"] = reference.effective_cwd
                group["workspace_cleanup_status"] = reference.workspace_cleanup_status
                group["workspace_cleanup_error"] = reference.workspace_cleanup_error
                group["workspace_unavailable"] = reference.workspace_unavailable
                group["workspace_unavailable_reason"] = reference.workspace_unavailable_reason
                group["workspace_fallback_cwd"] = reference.workspace_fallback_cwd
                group["workspace_created_at"] = reference.workspace_created_at
                group["workspace_health"] = reference.workspace_health
            if reference.source == "background":
                group["effective_cwd"] = reference.effective_cwd
            if reference.session_id:
                group["session_ids"].add(reference.session_id)
            if reference.transcript_path is not None:
                group["transcript_paths"].append(reference.transcript_path)
            if reference.background_record is not None:
                group["background_ids"].add(reference.background_record.bg_id)
            group["workspace_health"] = session._derive_workspace_health(
                workspace_mode=str(group["mode"]),
                workspace_cleanup_status=str(group["workspace_cleanup_status"]),
                workspace_unavailable=bool(group["workspace_unavailable"]),
            )
        inventory: list[dict[str, Any]] = []
        for item in grouped.values():
            inventory.append(
                {
                    **item,
                    "session_refs": len(item["session_ids"]),
                    "background_refs": len(item["background_ids"]),
                }
            )
        inventory.sort(key=lambda item: (item["mode"], item["label"], item["effective_cwd"]))
        return inventory

    def _render_workspace_inventory_entry(self, item: dict[str, Any]) -> str:
        parts = [
            f"workspace={item['mode']}",
            f"health={item['workspace_health']}",
            f"label={item['label']}",
            f"origin={item['original_cwd']}",
            f"cwd={item['effective_cwd']}",
            f"cleanup={item['workspace_cleanup_status']}",
            f"session_refs={item['session_refs']}",
            f"background_refs={item['background_refs']}",
        ]
        try:
            if not Path(str(item["effective_cwd"])).exists():
                parts.append("cwd_exists=no")
        except OSError:
            parts.append("cwd_exists=no")
        if item.get("workspace_cleanup_error"):
            parts.append(f"cleanup_error={item['workspace_cleanup_error']}")
        if item.get("workspace_fallback_cwd"):
            parts.append(f"fallback={item['workspace_fallback_cwd']}")
        return " ".join(parts)

    def _select_workspace_inventory_items(
        self,
        inventory: list[dict[str, Any]],
        selector: str,
    ) -> tuple[list[dict[str, Any]], str | None]:
        lowered = selector.strip().casefold()
        if lowered == "all":
            return list(inventory), None
        session_matches = [
            item
            for item in inventory
            if any(str(session_id).casefold().startswith(lowered) for session_id in item.get("session_ids", set()))
        ]
        if len(session_matches) == 1:
            return session_matches, None
        if len(session_matches) > 1:
            joined = ", ".join(self._render_workspace_inventory_entry(item) for item in session_matches)
            return [], f'workspace show failed: ambiguous session selector "{selector}": {joined}'
        label_matches = [item for item in inventory if str(item.get("label") or "").casefold() == lowered]
        if len(label_matches) == 1:
            return label_matches, None
        if len(label_matches) > 1:
            joined = ", ".join(self._render_workspace_inventory_entry(item) for item in label_matches)
            return [], f'workspace show failed: ambiguous workspace label "{selector}": {joined}'
        return [], f'workspace show failed: no isolated workspace matched "{selector}".'

    def preview_orphaned_workspace_cleanup(self) -> str:
        return self.workspace_cleanup_preview()

    def repair_isolated_workspaces(self, target: str) -> str:
        return self.workspace_repair(target)

    def apply_orphaned_workspace_cleanup(self, target: str) -> str:
        return self.workspace_cleanup_apply(target)

    def _select_orphaned_workspaces(
        self,
        orphans: list[OrphanedWorkspaceDiagnostic],
        selector: str,
    ) -> tuple[list[OrphanedWorkspaceDiagnostic], str | None]:
        lowered = selector.strip().casefold()
        if lowered == "all":
            return list(orphans), None
        matches = [item for item in orphans if item.label.casefold() == lowered]
        if not matches:
            return [], f'No orphaned isolated workspace matched "{selector}".'
        if len(matches) > 1:
            joined = ", ".join(self._render_orphaned_workspace_entry(item) for item in matches)
            return [], f'Ambiguous orphaned workspace label "{selector}": {joined}'
        return matches, None

    def _select_repair_targets(
        self,
        inventory: list[dict[str, Any]],
        selector: str,
    ) -> tuple[list[dict[str, Any]], str | None]:
        lowered = selector.strip().casefold()
        if lowered == "all":
            return list(inventory), None
        session_matches = [
            item
            for item in inventory
            if any(str(session_id).casefold().startswith(lowered) for session_id in item["session_ids"])
        ]
        if len(session_matches) == 1:
            return session_matches, None
        if len(session_matches) > 1:
            joined = ", ".join(self._render_workspace_inventory_entry(item) for item in session_matches)
            return [], f'repair failed: ambiguous session selector "{selector}": {joined}'
        label_matches = [item for item in inventory if str(item["label"]).casefold() == lowered]
        if len(label_matches) == 1:
            return label_matches, None
        if len(label_matches) > 1:
            joined = ", ".join(self._render_workspace_inventory_entry(item) for item in label_matches)
            return [], f'repair failed: ambiguous workspace label "{selector}": {joined}'
        return [], f'repair failed: no unavailable isolated workspace matched "{selector}".'

    def _apply_repaired_workspace_metadata(self, item: dict[str, Any], repaired_workspace: Any) -> None:
        session = self._session
        new_health = session._derive_workspace_health(
            workspace_mode=repaired_workspace.mode,
            workspace_cleanup_status="pending",
            workspace_unavailable=False,
        )
        if bool(item.get("has_current")) and session.state.session_id in item["session_ids"]:
            repaired_cwd = repaired_workspace.effective_cwd.resolve()
            session.config.cwd = repaired_cwd
            if session.config.mcp_config_path is not None:
                session.config.mcp_config_path = (
                    repaired_cwd / ".pyclaude" / "mcp_servers.json"
                ).resolve()
            if session.config.permission_config_path is not None:
                session.config.permission_config_path = (
                    repaired_cwd / ".pyclaude" / "permissions.json"
                ).resolve()
            session.state.original_cwd = str(Path(str(item["original_cwd"])).resolve())
            session.state.effective_cwd = str(repaired_cwd)
            session.state.workspace_mode = repaired_workspace.mode
            session.state.workspace_label = repaired_workspace.label
            session.state.workspace_created_at = repaired_workspace.created_at
            session.state.workspace_health = new_health
            session.state.workspace_cleanup_status = "pending"
            session.state.workspace_cleanup_error = None
            session.state.workspace_unavailable = False
            session.state.workspace_unavailable_reason = None
            session.state.workspace_fallback_cwd = session.state.original_cwd
            session.set_workspace_cleanup(
                lambda: cleanup_isolated_workspace(
                    IsolatedWorkspace(
                        mode=repaired_workspace.mode,
                        label=repaired_workspace.label,
                        original_cwd=Path(session.state.original_cwd or session.config.cwd).resolve(),
                        effective_cwd=repaired_workspace.effective_cwd.resolve(),
                        created_at=repaired_workspace.created_at,
                    )
                )
            )
            session.reload_project_context()
        for transcript_path in item["transcript_paths"]:
            update_transcript_workspace_metadata(
                transcript_path,
                original_cwd=str(Path(str(item["original_cwd"])).resolve()),
                effective_cwd=str(repaired_workspace.effective_cwd.resolve()),
                workspace_mode=repaired_workspace.mode,
                workspace_label=repaired_workspace.label,
                workspace_created_at=repaired_workspace.created_at,
                workspace_health=new_health,
                workspace_cleanup_status="pending",
                workspace_cleanup_error=None,
                workspace_unavailable=False,
                workspace_unavailable_reason=None,
                workspace_fallback_cwd=str(Path(str(item["original_cwd"])).resolve()),
            )
        for background_id in item["background_ids"]:
            update_background_session(
                self._workspace_anchor_cwd(),
                background_id,
                original_cwd=str(Path(str(item["original_cwd"])).resolve()),
                effective_cwd=str(repaired_workspace.effective_cwd.resolve()),
                workspace_mode=repaired_workspace.mode,
                workspace_label=repaired_workspace.label,
                workspace_created_at=repaired_workspace.created_at,
                workspace_health=new_health,
                workspace_cleanup_status="pending",
                workspace_cleanup_error=None,
                workspace_unavailable=False,
                workspace_unavailable_reason=None,
                workspace_fallback_cwd=str(Path(str(item["original_cwd"])).resolve()),
            )

    def _require_orphaned_workspace_cleanup_approval(
        self,
        targets: list[OrphanedWorkspaceDiagnostic],
        *,
        selector: str,
    ) -> None:
        session = self._session
        anchor = self._workspace_anchor_cwd().resolve()
        target_paths: list[str] = []
        for item in targets:
            try:
                target_paths.append(str(item.path.resolve().relative_to(anchor)))
            except ValueError:
                target_paths.append(str(item.path))
        labels = ",".join(item.label for item in targets)
        details = "\n".join(
            [
                "Delete orphaned isolated workspaces.",
                f"selector: {selector}",
                f"planned_deletions: {len(targets)}",
                "planned_targets:",
                *[f"- {self._render_orphaned_workspace_entry(item)}" for item in targets],
            ]
        )
        session.permission_manager.require_approval(
            ApprovalRequest(
                tool_name="workspace_cleanup",
                reason="Delete orphaned isolated workspaces from the local .pyclaude directory.",
                risk_level="delete",
                approval_key=f"workspace_cleanup_delete:{labels}",
                details=details,
                target_paths=tuple(target_paths),
            )
        )

    def current_workspace_action_bundle(self) -> dict[str, str]:
        session = self._session
        return _workspace_action_bundle(
            workspace_health=session.state.workspace_health,
            workspace_label=session.state.workspace_label,
            session_id=session.state.session_id,
        )

    def task_workspace_action_bundle(self, identifier: str) -> dict[str, str] | None:
        task = self._session.resolve_task(identifier)
        if task is None:
            return None
        metadata = task.metadata or {}
        return self._session._workspace_task_action_bundle(metadata)

    def task_workspace_detail_metadata(self, identifier: str) -> dict[str, Any] | None:
        task = self._session.resolve_task(identifier)
        if task is None:
            return None
        metadata = task.metadata or {}
        return self._session._workspace_task_detail_metadata(metadata)

    def describe_orphaned_workspaces(self) -> str:
        session = self._session
        anchor = session._workspace_anchor_cwd().resolve()
        references = session._workspace_reference_paths()
        orphans = session.orphaned_workspace_diagnostics()
        inventory = session._workspace_inventory()
        lines = [
            "isolated workspaces",
            f"workspace: {anchor}",
            f"tracked_isolated_workspaces: {len(inventory)}",
            f"referenced_isolated_workspaces: {len(references)}",
            f"orphaned_isolated_workspaces: {len(orphans)}",
        ]
        if inventory:
            lines.append("tracked:")
            for item in inventory:
                lines.append("- " + session._render_workspace_inventory_entry(item))
        if not orphans:
            if not inventory:
                lines.append("No isolated workspaces.")
            return "\n".join(lines)
        lines.append("orphans:")
        for item in orphans:
            lines.append("- " + session._render_orphaned_workspace_entry(item))
        return "\n".join(lines)

    def describe_current_workspace(self) -> str:
        session = self._session
        item = {
            "label": session.state.workspace_label,
            "mode": session.state.workspace_mode,
            "workspace_health": session.state.workspace_health,
            "original_cwd": session.state.original_cwd or str(session.config.cwd),
            "effective_cwd": session.state.effective_cwd or str(session.config.cwd),
            "workspace_cleanup_status": session.state.workspace_cleanup_status,
            "workspace_cleanup_error": session.state.workspace_cleanup_error,
            "workspace_unavailable_reason": session.state.workspace_unavailable_reason,
            "workspace_fallback_cwd": session.state.workspace_fallback_cwd,
            "workspace_created_at": session.state.workspace_created_at,
            "session_refs": 1,
            "background_refs": 0,
            "session_ids": {session.state.session_id} if session.state.session_id else set(),
            "background_ids": set(),
            "transcript_paths": [],
        }
        return "\n".join(
            self._render_workspace_detail_lines(
                item,
                title="Current workspace",
                action_bundle=session.current_workspace_action_bundle(),
            )
        )

    def describe_workspace_inventory_detail(self, selector: str) -> str:
        inventory = self._workspace_inventory()
        if not inventory:
            return "No isolated workspaces."
        selected, error = self._select_workspace_inventory_items(inventory, selector)
        if error is not None:
            return error
        lines = [
            "Isolated workspace detail",
            f"selected: {selector}",
            f"matched_workspaces: {len(selected)}",
        ]
        for index, item in enumerate(selected, start=1):
            if index > 1:
                lines.append("")
            lines.extend(
                self._render_workspace_detail_lines(
                    item,
                    title=f"Workspace {index}",
                    action_bundle=self._workspace_action_bundle_for_item(item),
                )
            )
        return "\n".join(lines)

    def workspace_cleanup_preview(self) -> str:
        session = self._session
        orphans = session.orphaned_workspace_diagnostics()
        lines = [
            "orphaned workspace cleanup plan",
            "dry_run: yes",
            f"planned_deletions: {len(orphans)}",
        ]
        if not orphans:
            lines.append("No orphaned isolated workspaces to clean up.")
            lines.append("deleted: 0")
            return "\n".join(lines)
        lines.append(
            f"cleanup planned | Would delete {len(orphans)} orphaned isolated workspace(s)."
        )
        lines.append("planned_targets:")
        for item in orphans:
            lines.append("- " + session._render_orphaned_workspace_entry(item))
        lines.append("deleted: 0")
        lines.append(
            "recommended_action: review these paths first; this command does not delete anything yet"
        )
        return "\n".join(lines)

    def workspace_repair(self, target: str) -> str:
        session = self._session
        raw = target.strip()
        if not raw:
            return "Usage: /workspaces repair <label|session|all>"
        inventory = [
            item
            for item in session._workspace_inventory()
            if item["workspace_health"] == "unavailable"
        ]
        if not inventory:
            return "\n".join(
                [
                    "isolated workspace repair",
                    "planned_repairs: 0",
                    "repaired: 0",
                    "No unavailable isolated workspaces to repair.",
                ]
            )
        selected, error = session._select_repair_targets(inventory, raw)
        if error is not None:
            return error
        planned_paths = [
            str(item.get("effective_cwd") or "")
            for item in selected
            if str(item.get("effective_cwd") or "").strip()
        ]
        task = session.task_manager.create(
            "workspace",
            "Repair unavailable isolated workspaces",
            workspace_action="repair",
            workspace_target=raw,
            workspace_health_before="unavailable",
            workspace_planned_paths=planned_paths,
            workspace_applied_paths=[],
        )
        session._set_workspace_task_progress(
            task.id,
            "repair planned",
            workspace_action="repair",
            workspace_target=raw,
            workspace_health_before="unavailable",
            workspace_planned_paths=planned_paths,
            workspace_applied_paths=[],
        )
        repaired: list[tuple[dict[str, Any], Any]] = []
        failed: list[tuple[dict[str, Any], str]] = []
        for index, item in enumerate(selected, start=1):
            label = str(item.get("label") or "").strip()
            current_applied_paths = [
                str(repaired_workspace.effective_cwd)
                for _item, repaired_workspace in repaired
            ]
            current_failure_reason = (
                " | ".join(
                    f"{str(failed_item.get('label') or failed_item.get('session_id') or 'workspace')}: {error_text}"
                    for failed_item, error_text in failed
                )
                if failed
                else None
            )
            session._set_workspace_task_progress(
                task.id,
                f"repair progress {index}/{len(selected)}",
                workspace_action="repair",
                workspace_target=raw,
                workspace_health_before="unavailable",
                workspace_planned_paths=planned_paths,
                workspace_applied_paths=current_applied_paths,
                workspace_failure_reason=current_failure_reason,
            )
            if not label:
                failed.append((item, "repair failed: workspace metadata is missing the label."))
                continue
            mode = str(item.get("mode") or "").strip()
            if mode not in {"snapshot", "worktree"}:
                failed.append(
                    (item, f"repair failed: unsupported workspace mode '{mode or 'unknown'}'.")
                )
                continue
            origin_value = str(item.get("original_cwd") or "").strip()
            if not origin_value:
                failed.append((item, "repair failed: workspace metadata is missing original_cwd."))
                continue
            origin_path = Path(origin_value)
            if not origin_path.exists():
                failed.append((item, f"repair failed: origin unavailable ({origin_path})."))
                continue
            try:
                repaired_workspace = repair_isolated_workspace(
                    origin_path,
                    label=label,
                    preferred_mode=mode,
                )
                session._apply_repaired_workspace_metadata(item, repaired_workspace)
                repaired.append((item, repaired_workspace))
            except Exception as exc:  # noqa: BLE001
                failed.append((item, f"repair backend failed: {type(exc).__name__}: {exc}"))
        output_lines = [
            "isolated workspace repair",
            f"planned_repairs: {len(selected)}",
            f"repaired: {len(repaired)}",
        ]
        output_lines.append(
            f"repair planned | Planned {len(selected)} isolated workspace repair(s)."
        )
        if repaired:
            output_lines.append("repaired_targets:")
            file_changes: list[WorkspaceFileChange] = []
            audit_suffixes: list[str] = []
            anchor = session._workspace_anchor_cwd().resolve()
            for item, repaired_workspace in repaired:
                repaired_health = session._derive_workspace_health(
                    workspace_mode=repaired_workspace.mode,
                    workspace_cleanup_status="pending",
                    workspace_unavailable=False,
                )
                rendered = session._render_workspace_inventory_entry(
                    {
                        **item,
                        "mode": repaired_workspace.mode,
                        "effective_cwd": str(repaired_workspace.effective_cwd),
                        "workspace_health": repaired_health,
                        "workspace_cleanup_status": "pending",
                        "workspace_cleanup_error": None,
                        "workspace_fallback_cwd": str(item["original_cwd"]),
                    }
                )
                output_lines.append("- " + rendered)
                try:
                    rel_path = repaired_workspace.effective_cwd.resolve().relative_to(anchor).as_posix()
                except ValueError:
                    rel_path = str(repaired_workspace.effective_cwd)
                file_changes.append(
                    WorkspaceFileChange(
                        path=rel_path,
                        existed_before=False,
                        before_content="",
                        after_content="",
                        action_kind="create",
                        change_mode="workspace_repair",
                    )
                )
                audit_suffixes.append(rel_path)
            session.record_workspace_audit_event(
                tool_name="workspace_repair",
                summary=f"Repaired {len(repaired)} isolated workspace(s).",
                file_changes=file_changes,
                audit_kind="workspace_audit",
            )
            output_lines.append("workspace_audit:")
            output_lines.append(
                "repair applied | "
                f"Repaired {len(repaired)} isolated workspace(s)."
                + (
                    f" | paths={', '.join(audit_suffixes[:2])}"
                    + (f", ... +{len(audit_suffixes) - 2}" if len(audit_suffixes) > 2 else "")
                    if audit_suffixes
                    else ""
                )
            )
        applied_paths = [str(repaired_workspace.effective_cwd) for _item, repaired_workspace in repaired]
        failure_reason = (
            " | ".join(
                f"{str(item.get('label') or item.get('session_id') or 'workspace')}: {error_text}"
                for item, error_text in failed
            )
            if failed
            else None
        )
        if failed:
            output_lines.append(f"failures: {len(failed)}")
            output_lines.append(
                f"repair failed | Failed to repair {len(failed)} isolated workspace(s)."
            )
            for item, error_text in failed:
                output_lines.append(
                    f"- {session._render_workspace_inventory_entry(item)} error={error_text}"
                )
        output = "\n".join(output_lines)
        if failed and not repaired:
            session._set_workspace_task_progress(
                task.id,
                "repair failed",
                workspace_action="repair",
                workspace_target=raw,
                workspace_health_before="unavailable",
                workspace_health_after="unavailable",
                workspace_planned_paths=planned_paths,
                workspace_applied_paths=applied_paths,
                workspace_failure_reason=failure_reason,
            )
            session.task_manager.fail(
                task.id,
                output,
                **session._workspace_task_runtime_metadata(
                    workspace_action="repair",
                    workspace_target=raw,
                    workspace_health_before="unavailable",
                    workspace_health_after="unavailable",
                    workspace_planned_paths=planned_paths,
                    workspace_applied_paths=applied_paths,
                    workspace_failure_reason=failure_reason,
                ),
            )
        else:
            final_health_after = "cleanup_pending" if repaired and not failed else "unavailable"
            session._set_workspace_task_progress(
                task.id,
                "repair applied" if repaired and not failed else "repair failed",
                workspace_action="repair",
                workspace_target=raw,
                workspace_health_before="unavailable",
                workspace_health_after=final_health_after,
                workspace_planned_paths=planned_paths,
                workspace_applied_paths=applied_paths,
                workspace_failure_reason=failure_reason,
            )
            session.task_manager.complete(
                task.id,
                output,
                **session._workspace_task_runtime_metadata(
                    workspace_action="repair",
                    workspace_target=raw,
                    workspace_health_before="unavailable",
                    workspace_health_after=final_health_after,
                    workspace_planned_paths=planned_paths,
                    workspace_applied_paths=applied_paths,
                    workspace_failure_reason=failure_reason,
                ),
            )
        return output

    def workspace_cleanup_apply(self, target: str) -> str:
        session = self._session
        raw = target.strip()
        if not raw:
            return "Usage: /workspaces cleanup [apply <label|all>]"
        orphans = session.orphaned_workspace_diagnostics()
        if not orphans:
            return "\n".join(
                [
                    "orphaned workspace cleanup apply",
                    "dry_run: no",
                    "planned_deletions: 0",
                    "deleted: 0",
                    "No orphaned isolated workspaces to clean up.",
                ]
            )
        selected, error = session._select_orphaned_workspaces(orphans, raw)
        if error is not None:
            return error
        session._require_orphaned_workspace_cleanup_approval(selected, selector=raw)
        planned_paths = [str(item.path) for item in selected]
        task = session.task_manager.create(
            "workspace",
            "Clean orphaned isolated workspaces",
            workspace_action="cleanup",
            workspace_target=raw,
            workspace_health_before="orphaned",
            workspace_planned_paths=planned_paths,
            workspace_applied_paths=[],
        )
        session._set_workspace_task_progress(
            task.id,
            "cleanup planned",
            workspace_action="cleanup",
            workspace_target=raw,
            workspace_health_before="orphaned",
            workspace_planned_paths=planned_paths,
            workspace_applied_paths=[],
        )
        anchor = session._workspace_anchor_cwd().resolve()
        deleted: list[OrphanedWorkspaceDiagnostic] = []
        failed: list[tuple[OrphanedWorkspaceDiagnostic, str]] = []
        for index, item in enumerate(selected, start=1):
            current_applied_paths = [str(path_item.path) for path_item in deleted]
            current_failure_reason = (
                " | ".join(f"{failed_item.label}: {error_text}" for failed_item, error_text in failed)
                if failed
                else None
            )
            session._set_workspace_task_progress(
                task.id,
                f"cleanup progress {index}/{len(selected)}",
                workspace_action="cleanup",
                workspace_target=raw,
                workspace_health_before="orphaned",
                workspace_planned_paths=planned_paths,
                workspace_applied_paths=current_applied_paths,
                workspace_failure_reason=current_failure_reason,
            )
            try:
                cleanup_orphaned_workspace(anchor, item)
                if item.path.exists():
                    failed.append((item, "Cleanup did not remove the workspace directory."))
                    continue
                deleted.append(item)
            except Exception as exc:  # noqa: BLE001
                failed.append((item, f"{type(exc).__name__}: {exc}"))
        if deleted:
            file_changes: list[WorkspaceFileChange] = []
            for item in deleted:
                try:
                    rel_path = item.path.resolve().relative_to(anchor).as_posix()
                except ValueError:
                    rel_path = str(item.path)
                file_changes.append(
                    WorkspaceFileChange(
                        path=rel_path,
                        existed_before=True,
                        before_content="",
                        after_content=None,
                        action_kind="delete",
                        change_mode="workspace_cleanup",
                    )
                )
            session.record_workspace_audit_event(
                tool_name="workspace_cleanup",
                summary=f"Deleted {len(deleted)} orphaned isolated workspace(s).",
                file_changes=file_changes,
                audit_kind="workspace_audit",
            )
        lines = [
            "orphaned workspace cleanup apply",
            "dry_run: no",
            f"planned_deletions: {len(selected)}",
            f"deleted: {len(deleted)}",
        ]
        lines.append(
            f"cleanup planned | Planned {len(selected)} orphaned isolated workspace deletion(s)."
        )
        if deleted:
            lines.append("deleted_targets:")
            for item in deleted:
                lines.append("- " + session._render_orphaned_workspace_entry(item))
            lines.append("workspace_audit:")
            path_preview = ", ".join(
                (
                    item.path.resolve().relative_to(anchor).as_posix()
                    if item.path.resolve().is_relative_to(anchor)
                    else str(item.path)
                )
                for item in deleted[:2]
            )
            if len(deleted) > 2:
                path_preview += f", ... +{len(deleted) - 2}"
            lines.append(
                f"cleanup applied | Deleted {len(deleted)} orphaned isolated workspace(s)."
                + (f" | paths={path_preview}" if path_preview else "")
            )
        applied_paths = [str(item.path) for item in deleted]
        failure_reason = (
            " | ".join(f"{item.label}: {error_text}" for item, error_text in failed)
            if failed
            else None
        )
        if failed:
            lines.append(f"failures: {len(failed)}")
            for item, error_text in failed:
                lines.append(f"- {session._render_orphaned_workspace_entry(item)} error={error_text}")
        output = "\n".join(lines)
        if failed and not deleted:
            session._set_workspace_task_progress(
                task.id,
                "cleanup failed",
                workspace_action="cleanup",
                workspace_target=raw,
                workspace_health_before="orphaned",
                workspace_health_after="orphaned",
                workspace_planned_paths=planned_paths,
                workspace_applied_paths=applied_paths,
                workspace_failure_reason=failure_reason,
            )
            session.task_manager.fail(
                task.id,
                output,
                **session._workspace_task_runtime_metadata(
                    workspace_action="cleanup",
                    workspace_target=raw,
                    workspace_health_before="orphaned",
                    workspace_health_after="orphaned",
                    workspace_planned_paths=planned_paths,
                    workspace_applied_paths=applied_paths,
                    workspace_failure_reason=failure_reason,
                ),
            )
        else:
            final_health_after = "healthy" if deleted and not failed else "orphaned"
            session._set_workspace_task_progress(
                task.id,
                "cleanup applied" if deleted and not failed else "cleanup failed",
                workspace_action="cleanup",
                workspace_target=raw,
                workspace_health_before="orphaned",
                workspace_health_after=final_health_after,
                workspace_planned_paths=planned_paths,
                workspace_applied_paths=applied_paths,
                workspace_failure_reason=failure_reason,
            )
            session.task_manager.complete(
                task.id,
                output,
                **session._workspace_task_runtime_metadata(
                    workspace_action="cleanup",
                    workspace_target=raw,
                    workspace_health_before="orphaned",
                    workspace_health_after=final_health_after,
                    workspace_planned_paths=planned_paths,
                    workspace_applied_paths=applied_paths,
                    workspace_failure_reason=failure_reason,
                ),
            )
        return output

    def runtime_cwd(self) -> Path:
        session = self._session
        if session.state.workspace_unavailable and session.state.workspace_fallback_cwd:
            return Path(session.state.workspace_fallback_cwd)
        return session.config.cwd

    def workspace_unavailable(self) -> bool:
        return bool(self._session.state.workspace_unavailable)
