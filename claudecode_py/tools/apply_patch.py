from __future__ import annotations

from dataclasses import dataclass

from ..permissions import ApprovalRequest
from ..state import WorkspaceFileChange
from .base import (
    BaseTool,
    count_workspace_change_actions,
    render_file_change_preview,
    render_pending_preview,
    resolve_workspace_path,
    truncate_detail_lines,
    workspace_change_to_approval,
)


@dataclass(slots=True)
class AddAction:
    path: str
    lines: list[str]


@dataclass(slots=True)
class DeleteAction:
    path: str


@dataclass(slots=True)
class UpdateHunk:
    lines: list[str]


@dataclass(slots=True)
class UpdateAction:
    path: str
    move_to: str | None
    hunks: list[UpdateHunk]


@dataclass(slots=True)
class HunkMatchDiagnostic:
    best_start: int | None
    mismatch_index: int | None
    expected_line: str | None
    actual_line: str | None
    candidate_lines: list[str]


class ApplyPatchTool(BaseTool):
    name = "apply_patch"
    description = (
        "Apply a multi-file patch using the Claude/Codex-style patch format. "
        "Prefer this for coordinated edits across one or more files."
    )
    read_only = False
    concurrency_safe = False
    input_schema = {
        "type": "object",
        "properties": {
            "patch": {
                "type": "string",
                "description": (
                    "Patch text in the format: *** Begin Patch ... *** End Patch, "
                    "with Add File / Update File / Delete File sections."
                ),
            }
        },
        "required": ["patch"],
    }

    def approval_request(self, tool_input: dict, ctx=None) -> ApprovalRequest:
        request = super().approval_request(tool_input, ctx)
        patch_text = tool_input["patch"]
        if ctx is None:
            request.details = truncate_detail_lines(patch_text, max_lines=18)
            return request
        try:
            actions = self._parse_patch(patch_text, tolerant=True)
            target_paths: list[str] = []
            changes: list[WorkspaceFileChange] = []
            for action in actions:
                if isinstance(action, AddAction):
                    target_paths.append(action.path)
                    preview = "\n".join(action.lines)
                    changes.append(
                        WorkspaceFileChange(
                            path=action.path,
                            existed_before=False,
                            before_content="",
                            after_content=preview,
                            action_kind="create",
                        )
                    )
                elif isinstance(action, DeleteAction):
                    target_paths.append(action.path)
                    path = resolve_workspace_path(ctx.cwd, action.path)
                    before = path.read_text(encoding="utf-8") if path.exists() else ""
                    changes.append(
                        WorkspaceFileChange(
                            path=action.path,
                            existed_before=path.exists(),
                            before_content=before,
                            after_content=None,
                            action_kind="delete",
                        )
                    )
                elif isinstance(action, UpdateAction):
                    target_paths.append(action.path)
                    if action.move_to is not None:
                        target_paths.append(action.move_to)
                    path = resolve_workspace_path(ctx.cwd, action.path)
                    before = path.read_text(encoding="utf-8") if path.exists() else ""
                    after = self._apply_update(before, action.hunks, action.path)
                    if action.move_to:
                        changes.append(
                            WorkspaceFileChange(
                                path=action.move_to,
                                existed_before=False,
                                before_content=before,
                                after_content=after,
                                action_kind="move",
                                source_path=action.path,
                                change_mode="patch move",
                            )
                        )
                    else:
                        changes.append(
                            WorkspaceFileChange(
                                path=action.path,
                                existed_before=True,
                                before_content=before,
                                after_content=after,
                                action_kind="update",
                                change_mode="patch update",
                            )
                        )
            request.target_paths = tuple(dict.fromkeys(target_paths))
            request.details = render_file_change_preview(
                [workspace_change_to_approval(change) for change in changes],
                max_lines=28,
            )
        except Exception as exc:  # noqa: BLE001
            request.details = render_pending_preview(
                "Pending file changes",
                summary_lines=[f"preview unavailable: {type(exc).__name__}: {exc}"],
                sections=[("patch", patch_text)],
                max_lines=20,
            )
        return request

    def execute(self, tool_input: dict, ctx):
        validator = getattr(ctx.session, "validate_plan_mode_tool_policy", None)
        if validator is not None:
            validator(self.name, tool_input)
        actions = self._parse_patch(tool_input["patch"])
        summaries: list[str] = []
        recorded_changes: list[WorkspaceFileChange] = []

        def append_change(change: WorkspaceFileChange) -> None:
            recorded_changes.append(change)

        for action in actions:
            if isinstance(action, AddAction):
                path = resolve_workspace_path(ctx.cwd, action.path)
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists():
                    raise FileExistsError(f"Cannot add file that already exists: {action.path}")
                after_content = "\n".join(action.lines)
                path.write_text(after_content, encoding="utf-8")
                append_change(
                    WorkspaceFileChange(
                        path=action.path,
                        existed_before=False,
                        before_content="",
                        after_content=after_content,
                        action_kind="create",
                    )
                )
                summaries.append(f"Created {action.path}")
                continue

            if isinstance(action, DeleteAction):
                path = resolve_workspace_path(ctx.cwd, action.path)
                if not path.exists():
                    raise FileNotFoundError(f"Cannot delete missing file: {action.path}")
                before_content = path.read_text(encoding="utf-8")
                path.unlink()
                append_change(
                    WorkspaceFileChange(
                        path=action.path,
                        existed_before=True,
                        before_content=before_content,
                        after_content=None,
                        action_kind="delete",
                    )
                )
                summaries.append(f"Deleted {action.path}")
                continue

            if isinstance(action, UpdateAction):
                source_path = resolve_workspace_path(ctx.cwd, action.path)
                if not source_path.exists():
                    raise FileNotFoundError(f"Cannot update missing file: {action.path}")
                original_text = source_path.read_text(encoding="utf-8")
                updated_text = self._apply_update(original_text, action.hunks, action.path)

                target_path = (
                    resolve_workspace_path(ctx.cwd, action.move_to)
                    if action.move_to is not None
                    else source_path
                )
                target_existed_before = target_path.exists()
                target_before_content = (
                    target_path.read_text(encoding="utf-8") if target_existed_before else ""
                )
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(updated_text, encoding="utf-8")
                if action.move_to is not None and target_path != source_path:
                    source_path.unlink()
                    append_change(
                        WorkspaceFileChange(
                            path=action.path,
                            existed_before=True,
                            before_content=original_text,
                            after_content=None,
                            action_kind="move_source",
                            source_path=action.move_to,
                            change_mode="patch move",
                        )
                    )
                    append_change(
                        WorkspaceFileChange(
                            path=action.move_to,
                            existed_before=target_existed_before,
                            before_content=target_before_content,
                            after_content=updated_text,
                            action_kind="move",
                            source_path=action.path,
                            change_mode="patch move",
                        )
                    )
                    summaries.append(f"Moved {action.path} -> {action.move_to}")
                else:
                    append_change(
                        WorkspaceFileChange(
                            path=action.path,
                            existed_before=True,
                            before_content=original_text,
                            after_content=updated_text,
                            action_kind="update",
                            change_mode="patch update",
                        )
                    )
                    summaries.append(f"Updated {action.path}")

        visible_changes = [
            change for change in recorded_changes if change.action_kind != "move_source"
        ]
        counts = count_workspace_change_actions(visible_changes)
        count_parts = [
            f"{name}={counts[name]}"
            for name in ("create", "update", "delete", "move")
            if counts[name]
        ]
        summary = (
            "Applied patch:\n" + "\n".join(f"- {item}" for item in summaries)
            if summaries
            else "Applied patch."
        )
        change_summary = (
            f"Applied patch ({len(visible_changes)} file(s); {' '.join(count_parts)})"
            if count_parts
            else f"Applied patch ({len(visible_changes)} file(s))"
        )
        ctx.session.record_workspace_change(
            tool_name=self.name,
            summary=change_summary,
            file_changes=recorded_changes,
        )
        return summary

    def _parse_patch(self, patch_text: str, *, tolerant: bool = False):
        lines = patch_text.splitlines()
        if not lines or lines[0].strip() != "*** Begin Patch":
            raise ValueError('Patch must start with "*** Begin Patch".')
        if lines[-1].strip() != "*** End Patch":
            raise ValueError('Patch must end with "*** End Patch".')

        actions: list[AddAction | DeleteAction | UpdateAction] = []
        index = 1
        while index < len(lines) - 1:
            line = lines[index]
            if line.startswith("*** Add File: "):
                path = line[len("*** Add File: ") :].strip()
                index += 1
                add_lines: list[str] = []
                while index < len(lines) - 1 and not lines[index].startswith("*** "):
                    current = lines[index]
                    if not current.startswith("+"):
                        raise ValueError(f"Add File expects '+' lines, got: {current}")
                    add_lines.append(current[1:])
                    index += 1
                actions.append(AddAction(path=path, lines=add_lines))
                continue

            if line.startswith("*** Delete File: "):
                path = line[len("*** Delete File: ") :].strip()
                actions.append(DeleteAction(path=path))
                index += 1
                continue

            if line.startswith("*** Update File: "):
                path = line[len("*** Update File: ") :].strip()
                index += 1
                move_to: str | None = None
                if index < len(lines) - 1 and lines[index].startswith("*** Move to: "):
                    move_to = lines[index][len("*** Move to: ") :].strip()
                    index += 1

                hunk_lines: list[str] = []
                hunks: list[UpdateHunk] = []
                while index < len(lines) - 1 and not lines[index].startswith("*** "):
                    current = lines[index]
                    if current.startswith("@@"):
                        if hunk_lines:
                            hunks.append(UpdateHunk(lines=hunk_lines))
                            hunk_lines = []
                        index += 1
                        continue
                    if current and current[0] not in {" ", "+", "-"}:
                        if tolerant:
                            hunk_lines.append(" " + current)
                            index += 1
                            continue
                        raise ValueError(f"Unsupported patch line in update section: {current}")
                    hunk_lines.append(current)
                    index += 1
                if hunk_lines:
                    hunks.append(UpdateHunk(lines=hunk_lines))
                if not hunks:
                    raise ValueError(f"Update File section for {path} has no patch hunks.")
                actions.append(UpdateAction(path=path, move_to=move_to, hunks=hunks))
                continue

            if line.strip():
                raise ValueError(f"Unsupported patch section: {line}")
            index += 1

        return actions

    def _apply_update(self, original_text: str, hunks: list[UpdateHunk], path: str) -> str:
        original_lines = original_text.splitlines()
        cursor = 0
        result: list[str] = []

        for hunk_index, hunk in enumerate(hunks, start=1):
            original_chunk = [line[1:] for line in hunk.lines if line.startswith((" ", "-"))]
            replacement_chunk = [line[1:] for line in hunk.lines if line.startswith((" ", "+"))]

            if original_chunk:
                start, diagnostic = self._find_chunk(original_lines, original_chunk, cursor)
                if start == -1:
                    raise ValueError(
                        self._format_hunk_match_error(
                            path=path,
                            hunk_index=hunk_index,
                            chunk=original_chunk,
                            diagnostic=diagnostic,
                        )
                    )
                end = start + len(original_chunk)
                result.extend(original_lines[cursor:start])
                result.extend(replacement_chunk)
                cursor = end
            else:
                result.extend(replacement_chunk)

        result.extend(original_lines[cursor:])
        return "\n".join(result)

    def _find_chunk(
        self, lines: list[str], chunk: list[str], start_index: int
    ) -> tuple[int, HunkMatchDiagnostic]:
        if not chunk:
            return start_index, HunkMatchDiagnostic(
                best_start=start_index,
                mismatch_index=None,
                expected_line=None,
                actual_line=None,
                candidate_lines=[],
            )
        max_start = len(lines) - len(chunk)
        best = HunkMatchDiagnostic(
            best_start=None,
            mismatch_index=None,
            expected_line=None,
            actual_line=None,
            candidate_lines=[],
        )
        best_prefix = -1
        for start in range(start_index, max_start + 1):
            candidate = lines[start : start + len(chunk)]
            if candidate == chunk:
                return start, HunkMatchDiagnostic(
                    best_start=start,
                    mismatch_index=None,
                    expected_line=None,
                    actual_line=None,
                    candidate_lines=candidate,
                )
            prefix = 0
            for index, (expected, actual) in enumerate(zip(chunk, candidate)):
                if expected == actual:
                    prefix += 1
                    continue
                if prefix > best_prefix:
                    best_prefix = prefix
                    best = HunkMatchDiagnostic(
                        best_start=start,
                        mismatch_index=index,
                        expected_line=expected,
                        actual_line=actual,
                        candidate_lines=candidate,
                    )
                break
            else:
                if len(candidate) > best_prefix:
                    best_prefix = len(candidate)
                    best = HunkMatchDiagnostic(
                        best_start=start,
                        mismatch_index=None,
                        expected_line=None,
                        actual_line=None,
                        candidate_lines=candidate,
                    )

        if best.best_start is None:
            preview = lines[start_index : start_index + min(len(chunk), 3)]
            best = HunkMatchDiagnostic(
                best_start=start_index if start_index < len(lines) else None,
                mismatch_index=0 if chunk else None,
                expected_line=chunk[0] if chunk else None,
                actual_line=preview[0] if preview else None,
                candidate_lines=preview,
            )
        return -1, best

    def _format_hunk_match_error(
        self,
        *,
        path: str,
        hunk_index: int,
        chunk: list[str],
        diagnostic: HunkMatchDiagnostic,
    ) -> str:
        lines = [
            f"Could not match patch hunk {hunk_index} in {path}.",
            "Expected hunk context:",
            *[f"  {line}" for line in chunk[:6]],
        ]
        if len(chunk) > 6:
            lines.append(f"  ... ({len(chunk) - 6} more line(s))")

        if diagnostic.best_start is not None:
            lines.append(f"Nearest candidate starts at line {diagnostic.best_start + 1}:")
            if diagnostic.candidate_lines:
                lines.extend(f"  {line}" for line in diagnostic.candidate_lines[:6])
                if len(diagnostic.candidate_lines) > 6:
                    lines.append(
                        f"  ... ({len(diagnostic.candidate_lines) - 6} more line(s))"
                    )
            if diagnostic.mismatch_index is not None:
                lines.append(
                    "First mismatch at hunk line "
                    f"{diagnostic.mismatch_index + 1}: expected "
                    f"{diagnostic.expected_line!r}, found {diagnostic.actual_line!r}."
                )
        else:
            lines.append("No nearby candidate block was found in the target file.")
        lines.append("Next steps:")
        lines.append("  1. Read the latest file contents and regenerate the patch with exact context lines.")
        lines.append("  2. If the target change is small, prefer edit_file with an exact old_text/new_text pair.")
        return "\n".join(lines)
