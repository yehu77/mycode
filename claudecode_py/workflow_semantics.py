from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContinuationSemantics:
    is_live_attachable: bool
    is_saved_resumable: bool
    category: str
    go_to_live_attach: str
    go_to_saved_resume: str
    stay_on_surface: str


def build_resume_commands(session_id: str) -> str:
    return (
        f"pyclaude --resume-session {session_id} repl"
        f" | pyclaude --resume-session {session_id} tui"
    )


def continuation_category(*, is_live_attachable: bool, is_saved_resumable: bool) -> str:
    if is_live_attachable:
        return "live attachable"
    if is_saved_resumable:
        return "saved resumable"
    return "inactive only"


def build_continuation_semantics(
    *,
    is_live_attachable: bool,
    is_saved_resumable: bool,
    live_attach_command: str | None,
    resume_session_id: str | None,
    stay_on_surface: str,
) -> ContinuationSemantics:
    go_to_live_attach = live_attach_command if is_live_attachable and live_attach_command else "none"
    go_to_saved_resume = (
        build_resume_commands(resume_session_id)
        if is_saved_resumable and resume_session_id
        else "none"
    )
    return ContinuationSemantics(
        is_live_attachable=is_live_attachable,
        is_saved_resumable=is_saved_resumable,
        category=continuation_category(
            is_live_attachable=is_live_attachable,
            is_saved_resumable=is_saved_resumable,
        ),
        go_to_live_attach=go_to_live_attach,
        go_to_saved_resume=go_to_saved_resume,
        stay_on_surface=stay_on_surface,
    )
