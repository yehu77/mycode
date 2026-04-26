from __future__ import annotations

from difflib import get_close_matches, unified_diff

from ..permissions import ApprovalRequest
from ..state import WorkspaceFileChange
from .base import BaseTool, render_diff_preview, render_pending_preview, resolve_workspace_path, truncate_detail_lines


class EditFileTool(BaseTool):
    name = "edit_file"
    description = "Apply targeted text edits to an existing workspace file. Prefer write_file for full-file creation or replacement."
    read_only = False
    concurrency_safe = False
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to the workspace."},
            "old_text": {"type": "string", "description": "Existing text to replace."},
            "new_text": {"type": "string", "description": "Replacement text or full file content."},
            "replace_all": {
                "type": "boolean",
                "description": "When using old_text/new_text, replace all matches instead of only the first.",
            },
            "edits": {
                "type": "array",
                "description": "Multiple targeted replacements to apply in order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                        "replace_all": {"type": "boolean"},
                    },
                    "required": ["old_text", "new_text"],
                },
            },
            "create_if_missing": {"type": "boolean", "description": "Create file if it does not exist."},
        },
        "required": ["path"],
    }

    def approval_request(self, tool_input: dict, ctx=None) -> ApprovalRequest:
        request = super().approval_request(tool_input, ctx)
        if ctx is None:
            return request
        path = resolve_workspace_path(ctx.cwd, tool_input["path"])
        rel_path = path.relative_to(ctx.cwd).as_posix()
        if not path.exists():
            request.details = render_pending_preview(
                "Pending file edit",
                summary_lines=[
                    f"path: {rel_path}",
                    f"create_if_missing: {bool(tool_input.get('create_if_missing', False))}",
                ],
            )
            return request
        current = path.read_text(encoding="utf-8")
        try:
            updated, replacements, used_multi_edit = self._compute_updated_content(current, tool_input)
            action = "multi-edit update" if used_multi_edit else "targeted replace"
            request.details = render_pending_preview(
                "Pending file edit",
                summary_lines=[
                    f"path: {rel_path}",
                    f"action: {action}",
                    f"replacements: {replacements}",
                ],
                sections=[("diff", render_diff_preview(rel_path, current, updated))],
                max_lines=20,
            )
        except Exception as exc:  # noqa: BLE001
            request.details = render_pending_preview(
                "Pending file edit",
                summary_lines=[
                    f"path: {rel_path}",
                    f"preview unavailable: {type(exc).__name__}: {exc}",
                ],
            )
        return request

    def execute(self, tool_input: dict, ctx):
        path = resolve_workspace_path(ctx.cwd, tool_input["path"])
        create_if_missing = bool(tool_input.get("create_if_missing", False))
        edits = tool_input.get("edits")
        top_level_new_text = tool_input.get("new_text")

        if not path.exists():
            if not create_if_missing:
                rel_path = path.relative_to(ctx.cwd).as_posix()
                raise FileNotFoundError(
                    f"File does not exist: {rel_path}\n"
                    "If you intended to create it, set create_if_missing=true or use write_file."
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            if top_level_new_text is None:
                raise ValueError("create_if_missing requires new_text for initial file content.")
            path.write_text(top_level_new_text, encoding="utf-8")
            rel_path = path.relative_to(ctx.cwd).as_posix()
            ctx.session.record_workspace_change(
                tool_name=self.name,
                summary=f"Created {rel_path}",
                file_changes=[
                    WorkspaceFileChange(
                        path=rel_path,
                        existed_before=False,
                        before_content="",
                        after_content=top_level_new_text,
                    )
                ],
            )
            return f"Created {rel_path}"

        current = path.read_text(encoding="utf-8")
        updated, replacements, used_multi_edit = self._compute_updated_content(current, tool_input)

        path.write_text(updated, encoding="utf-8")
        summary = self._summarize_edit(
            rel_path=path.relative_to(ctx.cwd).as_posix(),
            before=current,
            after=updated,
            replacements=replacements,
            used_multi_edit=used_multi_edit,
        )
        ctx.session.record_workspace_change(
            tool_name=self.name,
            summary=summary.splitlines()[0],
            file_changes=[
                WorkspaceFileChange(
                    path=path.relative_to(ctx.cwd).as_posix(),
                    existed_before=True,
                    before_content=current,
                    after_content=updated,
                )
            ],
        )
        return summary

    def _compute_updated_content(self, current: str, tool_input: dict) -> tuple[str, int, bool]:
        edits = tool_input.get("edits")
        top_level_new_text = tool_input.get("new_text")

        if not edits and top_level_new_text is None:
            raise ValueError("edit_file requires either edits[] or new_text.")

        updated = current
        replacements = 0
        used_multi_edit = bool(edits)

        if edits:
            for index, edit in enumerate(edits, start=1):
                old_text = edit["old_text"]
                new_text = edit["new_text"]
                replace_all = bool(edit.get("replace_all", False))
                updated, applied = self._apply_replace(updated, old_text, new_text, replace_all)
                if applied == 0:
                    raise ValueError(
                        self._build_missing_text_error(
                            current=updated,
                            old_text=old_text,
                            label=f"Edit #{index}",
                        )
                    )
                replacements += applied
        else:
            old_text = tool_input.get("old_text")
            if old_text is None:
                updated = top_level_new_text
            else:
                replace_all = bool(tool_input.get("replace_all", False))
                updated, replacements = self._apply_replace(
                    updated,
                    old_text,
                    top_level_new_text,
                    replace_all,
                )
                if replacements == 0:
                    raise ValueError(
                        self._build_missing_text_error(
                            current=updated,
                            old_text=old_text,
                            label="old_text",
                        )
                    )
        return updated, replacements, used_multi_edit

    def _apply_replace(
        self,
        content: str,
        old_text: str,
        new_text: str,
        replace_all: bool,
    ) -> tuple[str, int]:
        occurrences = content.count(old_text)
        if occurrences == 0:
            return content, 0
        if replace_all:
            return content.replace(old_text, new_text), occurrences
        return content.replace(old_text, new_text, 1), 1

    def _summarize_edit(
        self,
        *,
        rel_path: str,
        before: str,
        after: str,
        replacements: int,
        used_multi_edit: bool,
    ) -> str:
        change_label = "Applied edits" if used_multi_edit else "Updated"
        summary = f"{change_label} {rel_path}"
        if replacements:
            summary += f" ({replacements} replacement"
            if replacements != 1:
                summary += "s"
            summary += ")"

        diff_lines = list(
            unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
                lineterm="",
                n=2,
            )
        )
        if not diff_lines:
            return f"{summary}\n(no textual diff)"

        max_lines = 24
        rendered = diff_lines[:max_lines]
        if len(diff_lines) > max_lines:
            rendered.append("... diff truncated ...")
        return f"{summary}\n" + "\n".join(rendered)

    def _build_missing_text_error(self, *, current: str, old_text: str, label: str) -> str:
        lines = current.splitlines()
        stripped_target = old_text.strip()
        candidate_pool = [line.strip() for line in lines if line.strip()]
        close_matches = get_close_matches(stripped_target, candidate_pool, n=3, cutoff=0.45)

        message_lines = [f"{label} was not found in the target file."]
        if close_matches:
            message_lines.append("Closest matching lines:")
            message_lines.extend(f"  - {line}" for line in close_matches)
        elif stripped_target:
            substring_matches = [
                line.strip()
                for line in lines
                if stripped_target.casefold() in line.casefold() or line.strip().casefold() in stripped_target.casefold()
            ][:3]
            if substring_matches:
                message_lines.append("Nearby candidate lines:")
                message_lines.extend(f"  - {line}" for line in substring_matches)

        message_lines.append("Next step: read the file again and regenerate the exact old_text snippet before retrying.")
        return "\n".join(message_lines)
