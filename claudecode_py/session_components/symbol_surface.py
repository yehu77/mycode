from __future__ import annotations

from typing import Any

from ..integrations import (
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
from ..tools import FindReferencesTool
from ..tools.base import resolve_workspace_path


class SymbolSurfaceSessionComponent:
    def __init__(self, session: Any) -> None:
        self._session = session

    def locate_symbol(
        self,
        symbol: str,
        *,
        path: str = ".",
        max_results: int = 50,
    ) -> SymbolLookupResult:
        session = self._session
        base = resolve_workspace_path(session.config.cwd, path)
        matches = session._runtime_context.locate_symbols(
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
        session = self._session
        rendered = FindReferencesTool().execute(
            {
                "symbol": symbol,
                "path": path,
                "scope": scope,
                "max_results": max_results,
            },
            session.tool_context(),
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
        session = self._session
        resolved = resolve_workspace_path(session.config.cwd, path)
        rel_path = resolved.relative_to(session.config.cwd).as_posix()
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
        session = self._session
        resolved = resolve_workspace_path(session.config.cwd, path)
        rel_path = resolved.relative_to(session.config.cwd).as_posix()
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

    def _copy_jsonish_payload(self, payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if payload is None:
            return None
        copied: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, dict):
                copied[key] = dict(value)
            elif isinstance(value, list):
                copied[key] = [dict(item) if isinstance(item, dict) else item for item in value]
            else:
                copied[key] = value
        return copied

    def _remember_symbol_surface(self, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._session
        remembered = self._copy_jsonish_payload(payload) or {}
        session._current_symbol_surface = remembered
        return remembered

    def _format_editor_target_summary(self, target: dict[str, Any] | None) -> str:
        if not isinstance(target, dict):
            return "none"
        path = str(target.get("path") or "").strip()
        action = str(target.get("action") or "open_file").strip()
        line = int(target.get("line") or 1)
        label = str(target.get("label") or "").strip()
        summary = f"{action} {path}:{line}" if path else action
        if label:
            summary = f"{summary} ({label})"
        return summary

    def _format_symbol_candidate_summary(self, item: dict[str, Any] | None) -> str:
        if not isinstance(item, dict):
            return "none"
        if "action" in item:
            return self._format_editor_target_summary(item)
        path = str(item.get("path") or "").strip()
        line = int(item.get("line") or 1)
        symbol = str(item.get("symbol") or "").strip()
        kind = str(item.get("kind") or "").strip()
        text = str(item.get("text") or "").strip()
        label_parts = [part for part in (kind, symbol) if part]
        label = " ".join(label_parts).strip() or text
        summary = f"{path}:{line}" if path else symbol or "candidate"
        if label:
            summary = f"{summary} ({label})"
        return summary

    def _render_symbol_candidate_lines(
        self,
        *,
        title: str,
        items: list[dict[str, Any]],
        selected_index: Any,
    ) -> list[str]:
        if not items:
            return []
        lines = [title + ":"]
        try:
            selected = int(selected_index)
        except (TypeError, ValueError):
            selected = -1
        for index, item in enumerate(items):
            marker = ">" if index == selected else "-"
            lines.append(f"  {marker} {index + 1}. {self._format_symbol_candidate_summary(item)}")
        return lines

    def _symbol_surface_action_bundle_for_payload(
        self,
        payload: dict[str, Any] | None,
    ) -> dict[str, str] | None:
        if not isinstance(payload, dict) or not payload:
            return None
        kind = str(payload.get("surface_kind") or "").strip()
        symbol = str(payload.get("selected_symbol") or payload.get("symbol") or "").strip()
        primary_action = "none"
        secondary_action = "none"
        tertiary_action = "/symbol clear"
        navigation_target = payload.get("selected_navigation_target") or payload.get("navigation_target")
        if kind == "symbol_actions":
            if isinstance(payload.get("selected_definition"), dict):
                primary_action = "/symbol open primary"
            elif isinstance(navigation_target, dict):
                primary_action = "/symbol open primary"
            if isinstance(payload.get("selected_reference"), dict):
                secondary_action = "/symbol open secondary"
        else:
            if isinstance(navigation_target, dict):
                primary_action = "/symbol open primary"
        return {
            "primary_action": primary_action,
            "secondary_action": secondary_action,
            "tertiary_action": tertiary_action,
            "target": symbol or "none",
            "surface_kind": kind or "none",
        }

    def current_symbol_surface_payload(self) -> dict[str, Any] | None:
        return self._copy_jsonish_payload(self._session._current_symbol_surface)

    def current_symbol_surface_action_bundle(self) -> dict[str, str] | None:
        return self._symbol_surface_action_bundle_for_payload(self._session._current_symbol_surface)

    def _selected_symbol_navigation_target(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        target = payload.get("selected_navigation_target")
        if isinstance(target, dict):
            return dict(target)
        target = payload.get("navigation_target")
        if isinstance(target, dict):
            return dict(target)
        return None

    def _select_symbol_navigation_target(
        self,
        payload: dict[str, Any],
        target: dict[str, Any],
    ) -> dict[str, Any]:
        updated = self._copy_jsonish_payload(payload) or {}
        copied_target = dict(target)
        updated["selected_navigation_target"] = copied_target
        if str(updated.get("surface_kind") or "").strip() == "symbol_actions":
            updated["navigation_target"] = copied_target
        return self._remember_symbol_surface(updated)

    def _cycle_symbol_index(self, current: Any, count: int, delta: int) -> int | None:
        if count <= 0:
            return None
        try:
            current_index = int(current)
        except (TypeError, ValueError):
            current_index = 0
        return (current_index + delta) % count

    def _symbol_lookup_target_from_payload(
        self,
        payload: dict[str, Any],
        index: int,
    ) -> dict[str, Any] | None:
        matches = payload.get("matches")
        if not isinstance(matches, list) or not (0 <= index < len(matches)):
            return None
        match = matches[index]
        if not isinstance(match, dict):
            return None
        path = str(match.get("path") or "").strip()
        if not path:
            return None
        line = int(match.get("line") or 1)
        symbol = str(match.get("symbol") or payload.get("selected_symbol") or payload.get("symbol") or "").strip()
        kind = str(match.get("kind") or "").strip()
        owner = str(match.get("owner") or "").strip()
        label_parts = [part for part in (kind, symbol) if part]
        label = " ".join(label_parts).strip()
        if owner:
            label = f"{label} [{owner}]" if label else owner
        target = {
            "action": "open_symbol",
            "path": path,
            "line": line,
            "column": int(match.get("column") or 1),
            "symbol": symbol or None,
        }
        if label:
            target["label"] = label
        return target

    def _update_symbol_lookup_selection(self, *, delta: int) -> str:
        payload = self.current_symbol_surface_payload()
        if payload is None or str(payload.get("surface_kind") or "").strip() != "symbol_lookup":
            return "No active symbol lookup surface."
        matches = payload.get("matches")
        if not isinstance(matches, list) or not matches:
            return "No symbol lookup matches are available."
        next_index = self._cycle_symbol_index(payload.get("selected_match_index"), len(matches), delta)
        if next_index is None:
            return "No symbol lookup matches are available."
        selected_match = matches[next_index] if isinstance(matches[next_index], dict) else None
        target = self._symbol_lookup_target_from_payload(payload, next_index)
        updated = self._copy_jsonish_payload(payload) or {}
        updated["selected_match_index"] = next_index
        updated["selected_match"] = dict(selected_match) if isinstance(selected_match, dict) else None
        updated["selected_navigation_target"] = dict(target) if isinstance(target, dict) else None
        self._remember_symbol_surface(updated)
        return self._render_symbol_surface_text(updated)

    def _update_symbol_reference_selection(self, *, delta: int) -> str:
        payload = self.current_symbol_surface_payload()
        kind = str(payload.get("surface_kind") or "").strip() if isinstance(payload, dict) else ""
        if payload is None or kind not in {"symbol_references", "symbol_actions"}:
            return "No active symbol reference surface."
        references = payload.get("references")
        if not isinstance(references, list) or not references:
            return "No symbol references are available."
        next_index = self._cycle_symbol_index(payload.get("selected_reference_index"), len(references), delta)
        if next_index is None:
            return "No symbol references are available."
        selected_reference = references[next_index] if isinstance(references[next_index], dict) else None
        reference_targets = payload.get("reference_targets")
        target = None
        if isinstance(reference_targets, list) and 0 <= next_index < len(reference_targets):
            candidate = reference_targets[next_index]
            if isinstance(candidate, dict):
                target = dict(candidate)
        updated = self._copy_jsonish_payload(payload) or {}
        updated["selected_reference_index"] = next_index
        updated["selected_reference"] = dict(selected_reference) if isinstance(selected_reference, dict) else None
        if isinstance(target, dict):
            if kind == "symbol_references":
                updated["selected_navigation_target"] = target
            else:
                updated["selected_navigation_target"] = dict(updated.get("selected_definition") or target)
        self._remember_symbol_surface(updated)
        return self._render_symbol_surface_text(updated)

    def _update_symbol_definition_selection(self, *, delta: int) -> str:
        payload = self.current_symbol_surface_payload()
        if payload is None or str(payload.get("surface_kind") or "").strip() != "symbol_actions":
            return "No active symbol action surface."
        definitions = payload.get("definitions")
        if not isinstance(definitions, list) or not definitions:
            return "No symbol definitions are available."
        next_index = self._cycle_symbol_index(payload.get("selected_definition_index"), len(definitions), delta)
        if next_index is None:
            return "No symbol definitions are available."
        selected_definition = definitions[next_index] if isinstance(definitions[next_index], dict) else None
        updated = self._copy_jsonish_payload(payload) or {}
        updated["selected_definition_index"] = next_index
        updated["selected_definition"] = dict(selected_definition) if isinstance(selected_definition, dict) else None
        if isinstance(selected_definition, dict):
            updated["selected_navigation_target"] = dict(selected_definition)
            updated["navigation_target"] = dict(selected_definition)
        self._remember_symbol_surface(updated)
        return self._render_symbol_surface_text(updated)

    def symbol_surface_select_next_match(self) -> str:
        return self._update_symbol_lookup_selection(delta=1)

    def symbol_surface_select_prev_match(self) -> str:
        return self._update_symbol_lookup_selection(delta=-1)

    def symbol_surface_select_next_definition(self) -> str:
        return self._update_symbol_definition_selection(delta=1)

    def symbol_surface_select_prev_definition(self) -> str:
        return self._update_symbol_definition_selection(delta=-1)

    def symbol_surface_select_next_reference(self) -> str:
        return self._update_symbol_reference_selection(delta=1)

    def symbol_surface_select_prev_reference(self) -> str:
        return self._update_symbol_reference_selection(delta=-1)

    def _editor_target_payload(self, target: EditorTarget | None) -> dict[str, Any] | None:
        if target is None:
            return None
        return target.to_dict()

    def locate_symbol_surface_payload(
        self,
        symbol: str,
        *,
        path: str = ".",
        max_results: int = 50,
    ) -> dict[str, Any]:
        lookup = self.locate_symbol(symbol, path=path, max_results=max_results)
        selected_match = lookup.matches[0] if lookup.matches else None
        navigation_target = build_symbol_target(selected_match) if selected_match is not None else None
        payload = {
            **lookup.to_dict(),
            "surface_kind": "symbol_lookup",
            "match_count": len(lookup.matches),
            "selected_symbol": symbol,
            "selected_match": selected_match.to_dict() if selected_match is not None else None,
            "selected_match_index": 0 if selected_match is not None else None,
            "selected_navigation_target": self._editor_target_payload(navigation_target),
        }
        return self._remember_symbol_surface(payload)

    def collect_references_surface_payload(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "auto",
        max_results: int = 100,
    ) -> dict[str, Any]:
        lookup = self.collect_references(
            symbol,
            path=path,
            scope=scope,
            max_results=max_results,
        )
        targets = build_reference_targets(lookup)
        selected_reference = lookup.references[0] if lookup.references else None
        selected_target = targets.targets[0] if targets.targets else None
        payload = {
            **lookup.to_dict(),
            "surface_kind": "symbol_references",
            "reference_count": len(lookup.references),
            "selected_symbol": symbol,
            "selected_reference": selected_reference.to_dict() if selected_reference is not None else None,
            "selected_reference_index": 0 if selected_reference is not None else None,
            "selected_navigation_target": self._editor_target_payload(selected_target),
            "reference_targets": [item.to_dict() for item in targets.targets],
        }
        return self._remember_symbol_surface(payload)

    def build_symbol_action_surface_payload(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "workspace",
        max_definition_results: int = 50,
        max_reference_results: int = 100,
    ) -> dict[str, Any]:
        bundle = self.build_symbol_action_bundle(
            symbol,
            path=path,
            scope=scope,
            max_definition_results=max_definition_results,
            max_reference_results=max_reference_results,
        )
        selected_definition = bundle.definitions[0] if bundle.definitions else None
        selected_reference = bundle.references[0] if bundle.references else None
        navigation_target = selected_definition or selected_reference
        payload = {
            **bundle.to_dict(),
            "surface_kind": "symbol_actions",
            "definition_count": len(bundle.definitions),
            "reference_count": len(bundle.references),
            "selected_symbol": symbol,
            "selected_definition": self._editor_target_payload(selected_definition),
            "selected_definition_index": 0 if selected_definition is not None else None,
            "selected_reference": self._editor_target_payload(selected_reference),
            "selected_reference_index": 0 if selected_reference is not None else None,
            "navigation_target": self._editor_target_payload(navigation_target),
        }
        return self._remember_symbol_surface(payload)

    def _render_symbol_surface_text(self, payload: dict[str, Any] | None) -> str:
        if not isinstance(payload, dict) or not payload:
            return "No active symbol surface."
        action_bundle = self._symbol_surface_action_bundle_for_payload(payload) or {}
        lines = [
            "symbol_surface:",
            f"surface_kind: {payload.get('surface_kind') or 'none'}",
            f"selected_symbol: {payload.get('selected_symbol') or payload.get('symbol') or 'none'}",
            f"match_count: {payload.get('match_count') or 0}",
            f"definition_count: {payload.get('definition_count') or 0}",
            f"reference_count: {payload.get('reference_count') or 0}",
            "selected_match: " + self._format_editor_target_summary(payload.get("selected_navigation_target")),
        ]
        selected_match_index = payload.get("selected_match_index")
        match_count = int(payload.get("match_count") or 0)
        if selected_match_index is not None and match_count > 0:
            lines.append(f"selected_match_index: {int(selected_match_index) + 1}/{match_count}")
        selected_definition = self._format_editor_target_summary(payload.get("selected_definition"))
        if selected_definition != "none":
            lines.append(f"selected_definition: {selected_definition}")
            selected_definition_index = payload.get("selected_definition_index")
            definition_count = int(payload.get("definition_count") or 0)
            if selected_definition_index is not None and definition_count > 0:
                lines.append(
                    f"selected_definition_index: {int(selected_definition_index) + 1}/{definition_count}"
                )
        selected_reference = self._format_editor_target_summary(payload.get("selected_reference"))
        if selected_reference != "none":
            lines.append(f"selected_reference: {selected_reference}")
            selected_reference_index = payload.get("selected_reference_index")
            reference_count = int(payload.get("reference_count") or 0)
            if selected_reference_index is not None and reference_count > 0:
                lines.append(
                    f"selected_reference_index: {int(selected_reference_index) + 1}/{reference_count}"
                )
        navigation_target = self._format_editor_target_summary(
            payload.get("selected_navigation_target") or payload.get("navigation_target")
        )
        if navigation_target != "none":
            lines.append(f"navigation_target: {navigation_target}")
        match_items = [item for item in payload.get("matches", []) if isinstance(item, dict)]
        lines.extend(
            self._render_symbol_candidate_lines(
                title="matches",
                items=match_items,
                selected_index=payload.get("selected_match_index"),
            )
        )
        definition_items = [item for item in payload.get("definitions", []) if isinstance(item, dict)]
        lines.extend(
            self._render_symbol_candidate_lines(
                title="definitions",
                items=definition_items,
                selected_index=payload.get("selected_definition_index"),
            )
        )
        reference_items = [item for item in payload.get("references", []) if isinstance(item, dict)]
        lines.extend(
            self._render_symbol_candidate_lines(
                title="references",
                items=reference_items,
                selected_index=payload.get("selected_reference_index"),
            )
        )
        lines.extend(
            [
                f"selected_symbol_primary_action: {action_bundle.get('primary_action', 'none')}",
                f"selected_symbol_secondary_action: {action_bundle.get('secondary_action', 'none')}",
                f"selected_symbol_tertiary_action: {action_bundle.get('tertiary_action', '/symbol clear')}",
                f"selected_symbol_target: {action_bundle.get('target', 'none')}",
            ]
        )
        return "\n".join(lines)

    def describe_current_symbol_surface(self) -> str:
        return self._render_symbol_surface_text(self.current_symbol_surface_payload())

    def describe_symbol_lookup_surface(
        self,
        symbol: str,
        *,
        path: str = ".",
        max_results: int = 50,
    ) -> str:
        payload = self.locate_symbol_surface_payload(symbol, path=path, max_results=max_results)
        return self._render_symbol_surface_text(payload)

    def describe_symbol_reference_surface(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "auto",
        max_results: int = 100,
    ) -> str:
        payload = self.collect_references_surface_payload(
            symbol,
            path=path,
            scope=scope,
            max_results=max_results,
        )
        return self._render_symbol_surface_text(payload)

    def describe_symbol_action_surface(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "workspace",
        max_definition_results: int = 50,
        max_reference_results: int = 100,
    ) -> str:
        payload = self.build_symbol_action_surface_payload(
            symbol,
            path=path,
            scope=scope,
            max_definition_results=max_definition_results,
            max_reference_results=max_reference_results,
        )
        return self._render_symbol_surface_text(payload)

    def _open_symbol_surface_target(self, target: dict[str, Any] | None) -> str:
        session = self._session
        if not isinstance(target, dict):
            return "No symbol navigation target is available."
        if isinstance(session._current_symbol_surface, dict):
            self._select_symbol_navigation_target(session._current_symbol_surface, target)
        return "Selected symbol navigation target: " + self._format_editor_target_summary(target)

    def symbol_surface_primary_action(self) -> str:
        payload = self.current_symbol_surface_payload()
        if payload is None:
            return "No active symbol surface."
        kind = str(payload.get("surface_kind") or "").strip()
        if kind == "symbol_actions":
            return self._open_symbol_surface_target(payload.get("selected_definition"))
        return self._open_symbol_surface_target(self._selected_symbol_navigation_target(payload))

    def symbol_surface_secondary_action(self) -> str:
        payload = self.current_symbol_surface_payload()
        if payload is None:
            return "No active symbol surface."
        kind = str(payload.get("surface_kind") or "").strip()
        if kind == "symbol_actions":
            return self._open_symbol_surface_target(payload.get("selected_reference"))
        return "No secondary symbol navigation target is available."

    def clear_symbol_surface(self) -> str:
        self._session._current_symbol_surface = None
        return "Cleared active symbol surface."

    def _symbol_surface_config_fields(self) -> list[str]:
        payload = self.current_symbol_surface_payload()
        if payload is None:
            return [
                "symbol_surface_kind: none",
                "symbol_selected_match_index: none",
                "symbol_selected_definition_index: none",
                "symbol_selected_reference_index: none",
                "selected_symbol_primary_action: none",
                "selected_symbol_secondary_action: none",
                "selected_symbol_tertiary_action: /symbol clear",
                "selected_symbol_target: none",
            ]
        action_bundle = self._symbol_surface_action_bundle_for_payload(payload) or {}
        return [
            f"symbol_surface_kind: {payload.get('surface_kind') or 'none'}",
            f"symbol_selected_symbol: {payload.get('selected_symbol') or payload.get('symbol') or 'none'}",
            f"symbol_match_count: {payload.get('match_count') or 0}",
            f"symbol_definition_count: {payload.get('definition_count') or 0}",
            f"symbol_reference_count: {payload.get('reference_count') or 0}",
            "symbol_selected_match_index: "
            + (
                f"{int(payload.get('selected_match_index')) + 1}/{int(payload.get('match_count') or 0)}"
                if payload.get("selected_match_index") is not None and int(payload.get("match_count") or 0) > 0
                else "none"
            ),
            "symbol_selected_definition_index: "
            + (
                f"{int(payload.get('selected_definition_index')) + 1}/{int(payload.get('definition_count') or 0)}"
                if payload.get("selected_definition_index") is not None
                and int(payload.get("definition_count") or 0) > 0
                else "none"
            ),
            "symbol_selected_reference_index: "
            + (
                f"{int(payload.get('selected_reference_index')) + 1}/{int(payload.get('reference_count') or 0)}"
                if payload.get("selected_reference_index") is not None
                and int(payload.get("reference_count") or 0) > 0
                else "none"
            ),
            "symbol_selected_definition: "
            + self._format_editor_target_summary(payload.get("selected_definition")),
            "symbol_selected_reference: "
            + self._format_editor_target_summary(payload.get("selected_reference")),
            "symbol_navigation_target: "
            + self._format_editor_target_summary(
                payload.get("selected_navigation_target") or payload.get("navigation_target")
            ),
            f"selected_symbol_primary_action: {action_bundle.get('primary_action', 'none')}",
            f"selected_symbol_secondary_action: {action_bundle.get('secondary_action', 'none')}",
            f"selected_symbol_tertiary_action: {action_bundle.get('tertiary_action', '/symbol clear')}",
            f"selected_symbol_target: {action_bundle.get('target', 'none')}",
        ]
