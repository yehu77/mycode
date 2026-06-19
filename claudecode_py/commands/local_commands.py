from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
import shutil
from collections import Counter

from .registry import CommandExecution
from ..storage.transcript import list_transcripts, load_transcript, load_transcript_by_session_id


_INSTALLABLE_PROFILES: dict[str, tuple[str, ...]] = {
    "core": (),
    "anthropic": ("anthropic",),
    "openai": ("openai",),
    "tui": ("textual",),
    "mcp-remote": ("websockets",),
    "all": ("anthropic", "openai", "textual", "websockets"),
}

_ADVISOR_ALIASES = {
    "opus": "claude-3-opus-latest",
    "sonnet": "claude-3-7-sonnet-latest",
    "haiku": "claude-3-5-haiku-latest",
}
_ADVISOR_MODES = {"off", "final-review", "interactive-review"}
_TIMELINE_FILTERS = {"all", "plan", "scout", "execution", "advisor", "drift"}
_TIMELINE_DELTAS = {"none", "before-drift", "after-drift", "since-derived"}
_TIMELINE_FOCUS_PREFIX = "task:"
_TIMELINE_FOCUS_CHOICES = {"none", "scout", "execution"}
_TIMELINE_PHASES = {"none", "plan-setup", "scout-research", "execution-loop", "advisor-drift"}
_TIMELINE_COMPARE_MODES = {
    "none",
    "after-drift-vs-all",
    "execution-vs-scout",
    "active-vs-previous",
}
_PERMISSION_RULE_SCOPES = {"tool", "shell", "path", "risk"}
_CHANGES_COMMAND_USAGE = (
    "Usage: /changes [list|undo|redo|show <index-or-change-id>|show redo <index-or-change-id>|"
    "show <index-or-change-id> file <n>|show redo <index-or-change-id> file <n>|working-set]"
)
_TASKS_COMMAND_USAGE = "Usage: /tasks [list|active|changes|context|show <id>]"
_HISTORY_COMMAND_USAGE = "Usage: /history [all|messages|tasks|workspace|changes]"
_COMPACT_COMMAND_USAGE = "Usage: /compact [status|preview [instructions...]|<instructions...>]"
_REWIND_COMMAND_USAGE = "Usage: /rewind [list|show <n|boundary-id>|apply <n|boundary-id>]"
_SESSIONS_COMMAND_USAGE = (
    "Usage: /sessions [list|show latest|show <session-id-prefix>|show <session-id-prefix> summary|show <session-id-prefix> workspace]"
)
_CONFIG_COMMAND_USAGE = "Usage: /config [summary|workspace|runtime|permissions|plugins|mcp]"
_MODEL_COMMAND_USAGE = "Usage: /model [summary|capabilities|advisor]"
_STATUS_COMMAND_USAGE = "Usage: /status [summary|workspace|workflow|resume]"
_PLAN_SCOUTS_USAGE = "Usage: /planning scouts [<n> [file <m>]]"
_PLAN_EXECUTION_USAGE = "Usage: /planning execution [<n> [file <m>]]"
_PLANNING_COMMAND_USAGE = (
    "Usage: /planning [list|show [id|latest]|file <n>|scouts [<n> [file <m>]]|"
    "execution [<n> [file <m>]]|advisor|audit [artifact=<id|active|previous>]|"
    "timeline [all|plan|scout|execution|advisor|drift] "
    "[delta=none|before-drift|after-drift|since-derived] "
    "[phase=none|plan-setup|scout-research|execution-loop|advisor-drift] "
    "[focus=scout|execution|task:<id>] "
    "[compare=after-drift-vs-all|execution-vs-scout|active-vs-previous] "
    "[artifact=<id|active|previous>]|replay [latest|at=<n>] "
    "[all|plan|scout|execution|advisor|drift] "
    "[delta=none|before-drift|after-drift|since-derived] "
    "[phase=none|plan-setup|scout-research|execution-loop|advisor-drift] "
    "[focus=scout|execution|task:<id>] "
    "[compare=after-drift-vs-all|execution-vs-scout|active-vs-previous] "
    "[artifact=<id|active|previous>]|lineage|use <id>|revert <id>|clear|derive [goal]]"
)
_CONTEXT_COMMAND_USAGE = "Usage: /context [summary]"
_ADD_DIR_COMMAND_USAGE = "Usage: /add-dir <path>|list|clear|remove <n>"
_PROJECT_CONTEXT_COMMAND_USAGE = "Usage: /project-context [summary|memory|skills|plugins|reload-status]"
_FILES_COMMAND_USAGE = "Usage: /files [context|working-set|focused|changes|tasks|plan|explicit|auto|show <n>]"
_DIFF_COMMAND_USAGE = "Usage: /diff [summary|focused|working-set|change <index-or-change-id> [file <n>]]"
_LEGACY_PLAN_ARTIFACT_ACTIONS = {
    "list",
    "show",
    "file",
    "scouts",
    "execution",
    "advisor",
    "audit",
    "timeline",
    "replay",
    "lineage",
    "use",
    "revert",
    "clear",
    "derive",
}


def handle_install_command(_session: "Session", args: str) -> str:
    raw = args.strip().lower()
    if not raw or raw == "status":
        return _render_install_status()
    if raw not in _INSTALLABLE_PROFILES:
        choices = ", ".join(sorted(_INSTALLABLE_PROFILES))
        return f"Usage: /install [status|{choices}]"
    return _render_install_profile(raw)


def handle_advisor_command(session: "Session", args: str) -> str:
    raw = args.strip()
    if not raw:
        return session.describe_advisor()
    if raw.lower() == "status":
        return session.describe_advisor()
    if raw.lower() in {"off", "unset"}:
        return session.unset_advisor_model()
    lowered = raw.lower()
    if lowered.startswith("mode "):
        mode = _normalize_advisor_mode(raw.split(" ", 1)[1])
        if mode is None:
            return "Usage: /advisor [off|mode <final-review|interactive-review>|<model> [mode]]"
        if mode == "off":
            return session.unset_advisor_model()
        return session.set_advisor_mode(mode)
    parts = raw.split()
    if len(parts) >= 2:
        maybe_mode = _normalize_advisor_mode(parts[-1])
        if maybe_mode is not None:
            model = _normalize_advisor_model(" ".join(parts[:-1]))
            if maybe_mode == "off":
                return session.unset_advisor_model()
            return session.set_advisor_model(model, mode=maybe_mode)
    return session.set_advisor_model(_normalize_advisor_model(raw))


def handle_history_command(session: "Session", args: str) -> str:
    raw = args.strip().lower()
    if not raw or raw == "all":
        return session.describe_history(section="all")
    if raw in {"messages", "tasks", "workspace", "changes"}:
        return session.describe_history(section=raw)
    return _HISTORY_COMMAND_USAGE


def handle_compact_command(session: "Session", args: str) -> str:
    raw = args.strip()
    lowered = raw.lower()
    if not raw:
        return session.compact_history_into_context_summary()
    if lowered == "status":
        return session.describe_compact(section="status")
    if lowered == "preview":
        return session.describe_compact(section="preview")
    if lowered.startswith("preview "):
        return session.describe_compact(section="preview", instructions=raw.split(" ", 1)[1].strip())
    if lowered.startswith("status "):
        return _COMPACT_COMMAND_USAGE
    return session.compact_history_into_context_summary(instructions=raw)


def handle_rewind_command(session: "Session", args: str) -> str:
    raw = args.strip()
    lowered = raw.lower()
    if not raw or lowered == "list":
        return session.describe_rewind()
    if lowered.startswith("show "):
        return session.describe_rewind(raw)
    if lowered.startswith("apply "):
        selector = raw.split(" ", 1)[1].strip()
        if not selector:
            return _REWIND_COMMAND_USAGE
        return session.rewind_to_boundary(selector)
    return _REWIND_COMMAND_USAGE


def handle_sessions_command(session: "Session", args: str) -> str:
    raw = args.strip()
    lowered = raw.lower()
    if not raw or lowered == "list":
        return session.describe_saved_sessions()
    if not lowered.startswith("show "):
        return _SESSIONS_COMMAND_USAGE
    remainder = raw.split(" ", 1)[1].strip()
    if not remainder:
        return _SESSIONS_COMMAND_USAGE
    parts = remainder.split()
    selector = parts[0]
    if len(parts) == 1:
        return session.describe_saved_sessions(selector=selector, section="detail")
    if len(parts) == 2 and parts[1].lower() in {"summary", "workspace"}:
        return session.describe_saved_sessions(selector=selector, section=parts[1].lower())
    return _SESSIONS_COMMAND_USAGE


def handle_config_command(session: "Session", args: str) -> str:
    raw = args.strip().lower()
    if not raw or raw == "summary":
        return session.describe_config()
    if raw in {"workspace", "runtime", "permissions", "plugins", "mcp"}:
        return session.describe_config(section=raw)
    return _CONFIG_COMMAND_USAGE


def handle_model_command(session: "Session", args: str) -> str:
    raw = args.strip().lower()
    if not raw or raw == "summary":
        return session.describe_provider()
    if raw in {"capabilities", "advisor"}:
        return session.describe_provider(section=raw)
    return _MODEL_COMMAND_USAGE


def handle_status_command(session: "Session", args: str) -> str:
    raw = args.strip().lower()
    if not raw or raw == "summary":
        return session.describe_status()
    if raw in {"workspace", "workflow", "resume"}:
        return session.describe_status(section=raw)
    return _STATUS_COMMAND_USAGE


def handle_context_command(session: "Session", args: str) -> str:
    raw = args.strip().lower()
    if not raw or raw == "summary":
        return session.describe_context()
    return session.describe_context(section=raw)


def handle_add_dir_command(session: "Session", args: str) -> str:
    raw = args.strip()
    lowered = raw.lower()
    if not raw:
        return _ADD_DIR_COMMAND_USAGE
    if lowered == "list":
        return session.describe_explicit_context_paths()
    if lowered == "clear":
        return session.clear_explicit_context_paths()
    if lowered.startswith("remove "):
        value = raw.split(" ", 1)[1].strip()
        if not value.isdigit() or int(value) <= 0:
            return _ADD_DIR_COMMAND_USAGE
        return session.remove_explicit_context_path(int(value) - 1)
    return session.add_explicit_context_path(raw)


def handle_files_command(session: "Session", args: str) -> str:
    raw = args.strip()
    lowered = raw.lower()
    if not raw or lowered in {"context", "working-set"}:
        return session.describe_files()
    if lowered in {"focused", "changes", "tasks", "plan", "explicit", "auto"}:
        return session.describe_files(section=lowered)
    if lowered.startswith("show "):
        value = raw.split(" ", 1)[1].strip()
        if not value.isdigit() or int(value) <= 0:
            return _FILES_COMMAND_USAGE
        return session.describe_files(section="show", selected_index=int(value) - 1)
    return _FILES_COMMAND_USAGE


def handle_diff_command(session: "Session", args: str) -> str:
    raw = args.strip()
    lowered = raw.lower()
    if not raw or lowered == "summary":
        return session.describe_diff()
    if lowered in {"focused", "working-set"}:
        return session.describe_diff(section=lowered)
    if lowered.startswith("change "):
        remainder = raw.split(" ", 1)[1].strip()
        parts = remainder.split()
        if not parts:
            return _DIFF_COMMAND_USAGE
        selector = parts[0].strip()
        if not selector:
            return _DIFF_COMMAND_USAGE
        file_index = 0
        if len(parts) == 1:
            return session.describe_diff(section="change", selector=selector, file_index=file_index)
        if len(parts) == 3 and parts[1].lower() == "file" and parts[2].isdigit() and int(parts[2]) > 0:
            return session.describe_diff(
                section="change",
                selector=selector,
                file_index=int(parts[2]) - 1,
            )
        return _DIFF_COMMAND_USAGE
    return _DIFF_COMMAND_USAGE


def handle_project_context_command(session: "Session", args: str) -> str:
    raw = args.strip().lower()
    if not raw or raw == "summary":
        return session.describe_project_context()
    if raw in {"memory", "skills", "plugins", "reload-status"}:
        return session.describe_project_context(section=raw)
    return _PROJECT_CONTEXT_COMMAND_USAGE


def handle_plan_command(session: "Session", args: str) -> str | CommandExecution:
    raw = args.strip()
    if not raw:
        if session.in_plan_mode():
            return session.describe_current_plan_mode()
        plan_path = session.enter_plan_mode()
        return "Enabled plan mode.\n" + f"plan_file: {plan_path}"
    lowered = raw.lower()
    if lowered == "open":
        if not session.in_plan_mode():
            session.enter_plan_mode()
        return session.open_plan_file()
    action = raw.split(maxsplit=1)[0].lower()
    if action in _LEGACY_PLAN_ARTIFACT_ACTIONS:
        return handle_planning_command(session, raw, command_name="/plan")
    if session.in_plan_mode():
        return session.describe_current_plan_mode()
    session.enter_plan_mode()
    return CommandExecution(
        prompt=raw,
        progress_message="Planning in plan mode",
        metadata={
            "command_policy_name": "plan-mode",
            "command_policy_source": "repl:/plan",
            "command_kind": "plan-mode",
        },
    )


def handle_planning_command(
    session: "Session",
    args: str,
    *,
    command_name: str = "/planning",
) -> str:
    raw = args.strip()
    scouts_usage = f"Usage: {command_name} scouts [<n> [file <m>]]"
    execution_usage = f"Usage: {command_name} execution [<n> [file <m>]]"
    file_usage = f"Usage: {command_name} file <n>"
    if not raw:
        session.remember_plan_context_focus_payload(
            session.active_plan_file_context_payload(),
            file_index=0,
            preserve_current_focus=True,
        )
        return session.describe_active_plan(preserve_current_focus=True)
    if raw == "list":
        return session.describe_planning_artifacts()
    if raw == "audit":
        return session.describe_active_plan_audit()
    if raw == "clear":
        return session.clear_active_plan()
    if raw == "scouts":
        session.remember_plan_context_focus_payload(
            session.active_plan_scout_file_context_payload(selected_index=0),
            file_index=0,
            preserve_current_focus=True,
        )
        return session.describe_active_plan_scouts(preserve_current_focus=True)
    if raw.startswith("scouts "):
        parsed_scouts = _parse_plan_child_selector(raw.split()[1:], usage=scouts_usage)
        if isinstance(parsed_scouts, str):
            return parsed_scouts
        scout_count = session.active_plan_scout_count()
        if scout_count <= 0:
            return f"No active planning artifact for {command_name} scouts."
        selected_index = int(parsed_scouts["selected_index"])
        if selected_index >= scout_count:
            return scouts_usage
        file_index = parsed_scouts["file_index"]
        if file_index is not None:
            payload = session.active_plan_scout_file_context_payload(selected_index=selected_index)
            file_count = int(payload.get("file_context_file_count") or 0) if isinstance(payload, dict) else 0
            if file_count <= 0 or file_index >= file_count:
                return scouts_usage
            session.remember_plan_context_focus_payload(payload, file_index=int(file_index))
        else:
            session.remember_plan_context_focus_payload(
                session.active_plan_scout_file_context_payload(selected_index=selected_index),
                file_index=0,
                preserve_current_focus=True,
            )
        return session.describe_active_plan_scouts_at(
            selected_index,
            file_index=int(file_index or 0),
            preserve_current_focus=file_index is None,
        )
    if raw == "execution":
        session.remember_plan_context_focus_payload(
            session.active_plan_execution_file_context_payload(selected_index=0),
            file_index=0,
            preserve_current_focus=True,
        )
        return session.describe_active_plan_execution(preserve_current_focus=True)
    if raw.startswith("execution "):
        parsed_execution = _parse_plan_child_selector(raw.split()[1:], usage=execution_usage)
        if isinstance(parsed_execution, str):
            return parsed_execution
        execution_count = session.active_plan_execution_count()
        if execution_count <= 0:
            return f"No active planning artifact for {command_name} execution."
        selected_index = int(parsed_execution["selected_index"])
        if selected_index >= execution_count:
            return execution_usage
        file_index = parsed_execution["file_index"]
        if file_index is not None:
            payload = session.active_plan_execution_file_context_payload(selected_index=selected_index)
            file_count = int(payload.get("file_context_file_count") or 0) if isinstance(payload, dict) else 0
            if file_count <= 0 or file_index >= file_count:
                return execution_usage
            session.remember_plan_context_focus_payload(payload, file_index=int(file_index))
        else:
            session.remember_plan_context_focus_payload(
                session.active_plan_execution_file_context_payload(selected_index=selected_index),
                file_index=0,
                preserve_current_focus=True,
            )
        return session.describe_active_plan_execution_at(
            selected_index,
            file_index=int(file_index or 0),
            preserve_current_focus=file_index is None,
        )
    if raw == "advisor":
        session.remember_plan_context_focus_payload(
            session.active_plan_file_context_payload(),
            file_index=0,
            preserve_current_focus=True,
        )
        return session.describe_active_plan_advisor(preserve_current_focus=True)
    if raw == "timeline":
        return session.describe_active_plan_timeline()
    if raw == "replay":
        return session.describe_active_plan_replay(latest=True)
    if raw.startswith("file "):
        value = raw.split(" ", 1)[1].strip()
        if not value.isdigit() or int(value) <= 0:
            return file_usage
        payload = session.active_plan_file_context_payload()
        if payload is None:
            return session.describe_active_plan()
        file_count = int(payload.get("file_context_file_count") or 0)
        selected_index = int(value) - 1
        if file_count <= 0 or selected_index >= file_count:
            return file_usage
        session.remember_plan_context_focus_payload(payload, file_index=selected_index)
        return session.describe_active_plan(file_index=selected_index, preserve_current_focus=False)
    if raw.startswith("audit "):
        parsed_audit = _parse_plan_audit_args(raw.split()[1:])
        if isinstance(parsed_audit, str):
            return parsed_audit
        return session.describe_active_plan_audit(
            artifact_id=parsed_audit["artifact_id"],
        )
    if raw.startswith("timeline "):
        parsed = _parse_plan_slice_args(raw.split()[1:], allow_replay=False)
        if isinstance(parsed, str):
            return parsed
        return session.describe_active_plan_timeline(
            kind_filter=str(parsed["kind_filter"]),
            delta_mode=str(parsed["delta_mode"]),
            phase_filter=str(parsed["phase_filter"]),
            focus_mode=str(parsed["focus_mode"]),
            compare_mode=str(parsed["compare_mode"]),
            artifact_id=parsed["artifact_id"],
        )
    if raw.startswith("replay "):
        parsed = _parse_plan_slice_args(raw.split()[1:], allow_replay=True)
        if isinstance(parsed, str):
            return parsed
        return session.describe_active_plan_replay(
            kind_filter=str(parsed["kind_filter"]),
            delta_mode=str(parsed["delta_mode"]),
            phase_filter=str(parsed["phase_filter"]),
            focus_mode=str(parsed["focus_mode"]),
            compare_mode=str(parsed["compare_mode"]),
            artifact_id=parsed["artifact_id"],
            selected_index=int(parsed["selected_index"]),
            latest=bool(parsed["latest"]),
        )
    if raw == "lineage":
        return session.describe_active_plan_lineage()
    parts = raw.split(maxsplit=1)
    action = parts[0]
    remainder = parts[1] if len(parts) > 1 else ""
    if action == "show":
        return session.describe_planning_artifact(remainder or "latest")
    if action == "use":
        return session.use_planning_artifact(remainder)
    if action == "revert":
        return session.revert_to_planning_artifact(remainder or "latest")
    if action == "derive":
        return session.prepare_plan_derivation(remainder)
    return _PLANNING_COMMAND_USAGE.replace("/planning", command_name)


def _parse_plan_child_selector(tokens: list[str], *, usage: str) -> dict[str, int | None] | str:
    if not tokens:
        return {"selected_index": 0, "file_index": None}
    if len(tokens) == 1 and tokens[0].isdigit() and int(tokens[0]) > 0:
        return {"selected_index": int(tokens[0]) - 1, "file_index": None}
    if (
        len(tokens) == 3
        and tokens[0].isdigit()
        and int(tokens[0]) > 0
        and tokens[1].lower() == "file"
        and tokens[2].isdigit()
        and int(tokens[2]) > 0
    ):
        return {
            "selected_index": int(tokens[0]) - 1,
            "file_index": int(tokens[2]) - 1,
        }
    return usage


def _parse_plan_audit_args(tokens: list[str]) -> dict[str, str | None] | str:
    artifact_id: str | None = None
    for token in tokens:
        lowered = token.strip().lower()
        if lowered.startswith("artifact="):
            artifact_id = token.split("=", 1)[1].strip() or None
        else:
            return "Usage: /plan audit [artifact=<id|active|previous>]"
    return {"artifact_id": artifact_id}


def _parse_plan_slice_args(tokens: list[str], *, allow_replay: bool) -> dict[str, object] | str:
    kind_filter = "all"
    delta_mode = "none"
    focus_mode = "none"
    phase_filter = "none"
    compare_mode = "none"
    artifact_id: str | None = None
    latest = False
    selected_index = 0
    for token in tokens:
        lowered = token.strip().lower()
        if allow_replay and lowered == "latest":
            latest = True
        elif lowered in _TIMELINE_FILTERS:
            kind_filter = lowered
        elif lowered.startswith("at="):
            if not allow_replay:
                return _render_plan_slice_usage("timeline")
            value = lowered.split("=", 1)[1]
            if not value.isdigit() or int(value) <= 0:
                return _render_plan_slice_usage("replay")
            selected_index = int(value) - 1
        elif lowered.startswith("delta="):
            value = lowered.split("=", 1)[1]
            if value not in _TIMELINE_DELTAS:
                return _render_plan_slice_usage("replay" if allow_replay else "timeline")
            delta_mode = value
        elif lowered.startswith("phase="):
            value = lowered.split("=", 1)[1]
            if value not in _TIMELINE_PHASES:
                return _render_plan_slice_usage("replay" if allow_replay else "timeline")
            phase_filter = value
        elif lowered.startswith("focus="):
            value = lowered.split("=", 1)[1]
            if value not in _TIMELINE_FOCUS_CHOICES and not value.startswith(_TIMELINE_FOCUS_PREFIX):
                return _render_plan_slice_usage("replay" if allow_replay else "timeline")
            focus_mode = value
        elif lowered.startswith("compare="):
            value = lowered.split("=", 1)[1]
            if value not in _TIMELINE_COMPARE_MODES:
                return _render_plan_slice_usage("replay" if allow_replay else "timeline")
            compare_mode = value
        elif lowered.startswith("artifact="):
            artifact_id = token.split("=", 1)[1].strip() or None
        else:
            return _render_plan_slice_usage("replay" if allow_replay else "timeline")
    return {
        "kind_filter": kind_filter,
        "delta_mode": delta_mode,
        "focus_mode": focus_mode,
        "phase_filter": phase_filter,
        "compare_mode": compare_mode,
        "artifact_id": artifact_id,
        "latest": latest,
        "selected_index": selected_index,
    }


def _render_plan_slice_usage(kind: str) -> str:
    base = (
        f"Usage: /plan {kind} [all|plan|scout|execution|advisor|drift] "
        "[delta=none|before-drift|after-drift|since-derived] "
        "[phase=none|plan-setup|scout-research|execution-loop|advisor-drift] "
        "[focus=scout|execution|task:<id>] "
        "[compare=after-drift-vs-all|execution-vs-scout|active-vs-previous] "
        "[artifact=<id|active|previous>]"
    )
    if kind == "replay":
        return f"{base} [latest|at=<n>]"
    return base


def handle_insights_command(session: "Session", args: str) -> str:
    raw = args.strip()
    transcript_cwd = session.config.transcript_cwd or session.config.cwd
    if not raw or raw == "summary":
        return _render_workspace_insights(transcript_cwd)
    if raw == "latest":
        summaries = list_transcripts(transcript_cwd, limit=1)
        if not summaries:
            return "No saved sessions."
        return _render_session_insights(summaries[0].path)
    target = raw.removeprefix("session ").strip()
    if not target:
        return "Usage: /insights [summary|latest|<session-id-prefix>]"
    resolved = _resolve_transcript_path(transcript_cwd, target)
    if resolved is None:
        return f'No saved session found for "{target}".'
    return _render_session_insights(resolved)


def handle_permissions_command(session: "Session", args: str) -> str:
    raw = args.strip()
    if not raw or raw == "list":
        return session.describe_permissions()
    parts = raw.split(maxsplit=2)
    action = parts[0].lower()
    if action in {"allow", "ask", "deny"}:
        if len(parts) < 3 or parts[1].lower() not in _PERMISSION_RULE_SCOPES:
            return "Usage: /permissions <allow|ask|deny> <tool|shell|path|risk> <value>"
        return session.add_permission_rule(action, parts[1].lower(), parts[2])
    if action == "remove":
        if len(parts) < 3:
            return "Usage: /permissions remove <session|workspace> <index>"
        source = parts[1].lower()
        try:
            index = int(parts[2])
        except ValueError:
            return "Usage: /permissions remove <session|workspace> <index>"
        return session.remove_permission_rule(source, index)
    if action == "clear":
        target = parts[1].lower() if len(parts) > 1 else "session"
        return session.clear_permission_rules(target)
    if action == "save":
        return session.save_permission_rules()
    if action == "export":
        export_path = parts[1] if len(parts) > 1 else ""
        return session.export_permission_rules(export_path)
    if action == "reload":
        return session.reload_permission_rules()
    return (
        "Usage: /permissions [list|allow <tool|shell|path|risk> <value>|"
        "ask <tool|shell|path|risk> <value>|deny <tool|shell|path|risk> <value>|"
        "remove <session|workspace> <index>|clear [session|workspace|all]|save|export [path]|reload]"
    )


def handle_workspaces_command(session: "Session", args: str) -> str:
    raw = args.strip()
    lowered = raw.lower()
    if not raw or lowered == "list":
        return session.describe_orphaned_workspaces()
    if lowered == "current":
        return session.describe_current_workspace()
    if lowered.startswith("show "):
        selector = raw.split(" ", 1)[1].strip()
        if not selector:
            return (
                "Usage: /workspaces [list|current|show <label|session-id|all>|cleanup|cleanup apply <label|all>|repair <label|session|all>]\n"
                "health vocabulary: healthy | unavailable | orphaned | cleanup_pending | cleanup_failed"
            )
        return session.describe_workspace_inventory_detail(selector)
    if lowered == "cleanup":
        return session.workspace_cleanup_preview()
    if lowered.startswith("cleanup apply "):
        return session.workspace_cleanup_apply(raw.split(" ", 2)[2].strip())
    if lowered.startswith("repair "):
        return session.workspace_repair(raw.split(" ", 1)[1].strip())
    return (
        "Usage: /workspaces [list|current|show <label|session-id|all>|cleanup|cleanup apply <label|all>|repair <label|session|all>]\n"
        "health vocabulary: healthy | unavailable | orphaned | cleanup_pending | cleanup_failed"
    )


def handle_tasks_command(session: "Session", args: str) -> str:
    raw = args.strip()
    lowered = raw.lower()
    if not raw or lowered == "list":
        return session.describe_tasks()
    if lowered in {"active", "changes", "context"}:
        return session.describe_tasks(mode=lowered)
    if lowered.startswith("show "):
        identifier = raw.split(" ", 1)[1].strip()
        if not identifier:
            return _TASKS_COMMAND_USAGE
        session.remember_task_context_focus(identifier, file_index=0, preserve_current_focus=True)
        return session.describe_task_detail(identifier, preserve_current_focus=True)
    return _TASKS_COMMAND_USAGE


def handle_changes_command(session: "Session", args: str) -> str:
    raw = args.strip()
    lowered = raw.lower()
    if not raw or lowered == "list":
        return session.describe_recent_changes()
    if lowered == "undo":
        return session.describe_change_stack(redo=False)
    if lowered == "redo":
        return session.describe_change_stack(redo=True)
    if lowered == "working-set":
        return session.describe_working_set()
    if lowered.startswith("show "):
        remainder = raw.split(" ", 1)[1].strip()
        redo = False
        if remainder.lower().startswith("redo "):
            redo = True
            remainder = remainder.split(" ", 1)[1].strip()
        if not remainder:
            return _CHANGES_COMMAND_USAGE
        file_index = 0
        selector = remainder
        parts = remainder.split()
        if len(parts) >= 3 and parts[-2].lower() == "file":
            selector = " ".join(parts[:-2]).strip()
            if not selector or not parts[-1].isdigit() or int(parts[-1]) <= 0:
                return _CHANGES_COMMAND_USAGE
            file_index = int(parts[-1]) - 1
        elif len(parts) > 1:
            return _CHANGES_COMMAND_USAGE
        selected_index = session.resolve_change_stack_index(selector, redo=redo)
        if selected_index is None:
            return _CHANGES_COMMAND_USAGE
        file_count = session.selected_change_file_count(index=selected_index, limit=10, redo=redo)
        if file_count <= 0:
            session.remember_selected_change_context_focus(
                index=selected_index,
                file_index=0,
                redo=redo,
                preserve_current_focus=True,
            )
            return session.selected_change_detail(
                index=selected_index,
                limit=10,
                redo=redo,
                preserve_current_focus=True,
            )
        if file_index >= file_count:
            return _CHANGES_COMMAND_USAGE
        preserve_current_focus = len(parts) == 1
        session.remember_selected_change_context_focus(
            index=selected_index,
            file_index=file_index,
            redo=redo,
            preserve_current_focus=preserve_current_focus,
        )
        return session.selected_change_detail(
            index=selected_index,
            file_index=file_index,
            limit=10,
            redo=redo,
            preserve_current_focus=preserve_current_focus,
        )
    return _CHANGES_COMMAND_USAGE


def handle_symbol_command(session: "Session", args: str) -> str:
    raw = args.strip()
    if not raw or raw.lower() == "status":
        return session.describe_current_symbol_surface()
    lowered = raw.lower()
    if lowered == "clear":
        return session.clear_symbol_surface()
    if lowered == "open" or lowered == "open primary":
        return session.symbol_surface_primary_action()
    if lowered == "open secondary":
        return session.symbol_surface_secondary_action()
    if lowered == "next match":
        return session.symbol_surface_select_next_match()
    if lowered == "prev match":
        return session.symbol_surface_select_prev_match()
    if lowered == "next definition":
        return session.symbol_surface_select_next_definition()
    if lowered == "prev definition":
        return session.symbol_surface_select_prev_definition()
    if lowered == "next reference":
        return session.symbol_surface_select_next_reference()
    if lowered == "prev reference":
        return session.symbol_surface_select_prev_reference()
    parts = raw.split(maxsplit=1)
    if len(parts) != 2:
        return (
            "Usage: /symbol [status|clear|open [primary|secondary]|next|prev "
            "[match|definition|reference]|locate <symbol>|references <symbol>|actions <symbol>]"
        )
    verb, target = parts[0].lower(), parts[1].strip()
    if not target:
        return "Usage: /symbol [status|clear|open [primary|secondary]|locate <symbol>|references <symbol>|actions <symbol>]"
    if verb == "locate":
        return session.describe_symbol_lookup_surface(target)
    if verb in {"references", "refs"}:
        return session.describe_symbol_reference_surface(target)
    if verb == "actions":
        return session.describe_symbol_action_surface(target)
    return "Usage: /symbol [status|clear|open [primary|secondary]|locate <symbol>|references <symbol>|actions <symbol>]"


def _render_install_status() -> str:
    root = _project_root()
    lines = [
        "python-claudecode install status",
        f"project_root: {root}",
        f"pyclaude_on_path: {'yes' if shutil.which('pyclaude') else 'no'}",
        "profiles:",
    ]
    for name in ("core", "anthropic", "openai", "tui", "mcp-remote", "all"):
        lines.append(f"- {_render_install_profile_line(name)}")
    lines.append("This /install command reports editable install commands for the Python clone.")
    return "\n".join(lines)


def _render_install_profile(name: str) -> str:
    return "\n".join(
        [
            f"profile: {name}",
            f"command: {_pip_install_command(name)}",
            f"status: {_profile_status(name)}",
        ]
    )


def _render_install_profile_line(name: str) -> str:
    return f"{name} status={_profile_status(name)} command={_pip_install_command(name)}"


def _profile_status(name: str) -> str:
    requirements = _INSTALLABLE_PROFILES[name]
    if not requirements:
        return "ready"
    missing = [module for module in requirements if find_spec(module) is None]
    if not missing:
        return "installed"
    return "missing:" + ",".join(missing)


def _pip_install_command(name: str) -> str:
    root = _project_root()
    if name == "core":
        return f"pip install -e {root}"
    return f"pip install -e {root}[{name}]"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _normalize_advisor_model(value: str) -> str:
    lowered = value.strip().lower()
    return _ADVISOR_ALIASES.get(lowered, value.strip())


def _normalize_advisor_mode(value: str) -> str | None:
    lowered = value.strip().lower()
    return lowered if lowered in _ADVISOR_MODES else None


def _render_workspace_insights(cwd: Path) -> str:
    summaries = list_transcripts(cwd)
    if not summaries:
        return "No saved sessions."
    states = [load_transcript(summary.path) for summary in summaries]
    provider_counts = Counter(summary.provider or "unknown" for summary in summaries)
    model_counts = Counter(summary.model or "unknown" for summary in summaries)
    tool_counts = Counter()
    total_changes = 0
    sessions_with_changes = 0
    sessions_with_advisor = 0
    sessions_with_planning_artifacts = 0
    sessions_with_constraint_triggers = 0
    sessions_with_plan_executions = 0
    sessions_with_plan_drift = 0
    sessions_with_manual_plugin_overrides = 0
    advisor_revision_count = 0
    advisor_block_count = 0
    constraint_trigger_count = 0
    plan_execution_count = 0
    plan_drift_count = 0
    block_source_counts = Counter()
    ultraplan_runs = 0
    ultraplan_with_read_only_subagents = 0
    derived_plans = 0
    derived_from_drift_count = 0
    for state in states:
        counts = _collect_tool_counts(state.messages)
        tool_counts.update(counts)
        total_changes += len(state.recent_change_sets)
        if state.recent_change_sets:
            sessions_with_changes += 1
        if state.advisor_model:
            sessions_with_advisor += 1
        if state.planning_artifact_history or state.recent_planning_artifacts:
            sessions_with_planning_artifacts += 1
        if state.constraint_trigger_count:
            sessions_with_constraint_triggers += 1
        if state.plan_execution_count:
            sessions_with_plan_executions += 1
        if state.plan_drift_count:
            sessions_with_plan_drift += 1
        if state.enabled_plugin_names or state.disabled_plugin_names:
            sessions_with_manual_plugin_overrides += 1
        advisor_revision_count += sum(1 for item in state.advisor_review_history if item.status == "revise")
        advisor_block_count += sum(1 for item in state.advisor_review_history if item.status == "block")
        constraint_trigger_count += state.constraint_trigger_count
        plan_execution_count += state.plan_execution_count
        plan_drift_count += state.plan_drift_count
        block_source_counts.update(
            item.checkpoint
            for item in state.advisor_review_history
            if item.status == "block"
        )
        planning_artifacts = state.planning_artifact_history or state.recent_planning_artifacts
        ultraplan_items = [item for item in planning_artifacts if item.kind == "ultraplan"]
        ultraplan_runs += len(ultraplan_items)
        ultraplan_with_read_only_subagents += sum(1 for item in ultraplan_items if item.used_read_only_subagents)
        derived_plans += sum(1 for item in planning_artifacts if item.supersedes_artifact_id)
        derived_from_drift_count += sum(1 for item in planning_artifacts if item.derived_from_drift)
    total_messages = sum(summary.message_count for summary in summaries)
    avg_messages = total_messages / len(summaries)
    lines = [
        "workspace insights",
        f"workspace: {cwd}",
        f"sessions: {len(summaries)}",
        f"messages: {total_messages}",
        f"avg_messages_per_session: {avg_messages:.1f}",
        f"sessions_with_context_summary: {sum(1 for item in summaries if item.context_summary_present)}",
        f"sessions_with_recorded_changes: {sessions_with_changes}",
        f"recorded_change_sets: {total_changes}",
        f"sessions_with_advisor: {sessions_with_advisor}",
        f"sessions_with_planning_artifacts: {sessions_with_planning_artifacts}",
        f"sessions_with_constraint_triggers: {sessions_with_constraint_triggers}",
        f"constraint_trigger_sessions: {sessions_with_constraint_triggers}",
        f"sessions_with_plan_executions: {sessions_with_plan_executions}",
        f"sessions_with_plan_drift: {sessions_with_plan_drift}",
        f"sessions_with_manual_plugin_overrides: {sessions_with_manual_plugin_overrides}",
        f"advisor_revisions: {advisor_revision_count}",
        f"advisor_blocks: {advisor_block_count}",
        f"constraint_triggers: {constraint_trigger_count}",
        f"plan_executions: {plan_execution_count}",
        f"plan_drifts: {plan_drift_count}",
        "block_sources: " + _format_counter(block_source_counts),
        f"ultraplan_runs: {ultraplan_runs}",
        f"ultraplan_with_read_only_subagents: {ultraplan_with_read_only_subagents}",
        f"derived_plans: {derived_plans}",
        f"derived_from_drift_count: {derived_from_drift_count}",
        "providers: " + _format_counter(provider_counts),
        "models: " + _format_counter(model_counts),
        "top_tools: " + _format_counter(tool_counts, limit=5),
        "recent_sessions:",
    ]
    for summary in summaries[:3]:
        lines.append(
            f"- {summary.session_id} updated={summary.updated_at or summary.created_at or 'unknown'} "
            f"provider={summary.provider or 'unknown'} model={summary.model or 'unknown'} "
            f"messages={summary.message_count}"
        )
    return "\n".join(lines)


def _render_session_insights(path: Path) -> str:
    state = load_transcript(path)
    tool_counts = _collect_tool_counts(state.messages)
    role_counts = Counter(str(message.get("role", "unknown")) for message in state.messages)
    changed_files = {
        file_change.path
        for change in state.recent_change_sets
        for file_change in change.files
    }
    lines = [
        "session insights",
        f"path: {path}",
        f"session_id: {state.session_id}",
        f"created_at: {state.created_at}",
        f"updated_at: {state.updated_at or 'unknown'}",
        f"context_summary: {'yes' if state.context_summary else 'no'}",
        f"advisor_model: {state.advisor_model or 'none'}",
        f"advisor_mode: {state.advisor_mode}",
        f"active_execution_constraint: {state.active_execution_constraint}",
        f"plan_executions: {state.plan_execution_count}",
        f"plan_drifts: {state.plan_drift_count}",
        f"user_messages: {role_counts.get('user', 0)}",
        f"assistant_messages: {role_counts.get('assistant', 0)}",
        f"tool_calls: {sum(tool_counts.values())}",
        f"recorded_change_sets: {len(state.recent_change_sets)}",
        f"changed_files: {len(changed_files)}",
        "top_tools: " + _format_counter(tool_counts, limit=5),
    ]
    if state.advisor_last_result is not None:
        lines.append(
            "advisor_last_result: "
            f"{state.advisor_last_result.checkpoint}/{state.advisor_last_result.status}"
        )
        if state.advisor_last_result.risk_flags:
            lines.append("advisor_risk_flags: " + ", ".join(state.advisor_last_result.risk_flags))
    if state.advisor_review_history:
        lines.append(f"advisor_reviews: {len(state.advisor_review_history)}")
        lines.append(
            "advisor_block_count: "
            + str(sum(1 for item in state.advisor_review_history if item.status == "block"))
        )
    lines.append(f"constraint_triggers: {state.constraint_trigger_count}")
    if state.last_plan_drift_status:
        lines.append("last_plan_drift_status: " + state.last_plan_drift_status)
    if state.last_plan_drift_reason:
        lines.append("last_plan_drift_reason: " + state.last_plan_drift_reason)
    if state.constraint_source:
        lines.append("constraint_source: " + state.constraint_source)
    if state.constraint_reason:
        lines.append("constraint_reason: " + state.constraint_reason)
    planning_artifacts = state.planning_artifact_history or state.recent_planning_artifacts
    if planning_artifacts:
        lines.append(f"planning_artifacts: {len(planning_artifacts)}")
        latest_artifact = planning_artifacts[-1]
        lines.append("latest_planning_artifact: " + f"{latest_artifact.kind} goal={latest_artifact.goal}")
        lines.append("active_planning_artifact_id: " + str(state.active_planning_artifact_id or "none"))
        lines.append(
            "latest_planning_artifact_read_only_subagents: "
            f"{'yes' if latest_artifact.used_read_only_subagents else 'no'}"
        )
        lines.append(
            "latest_planning_artifact_derived_from_drift: "
            + ("yes" if latest_artifact.derived_from_drift else "no")
        )
        if latest_artifact.derivation_reason:
            lines.append("latest_planning_artifact_derivation_reason: " + latest_artifact.derivation_reason)
        if latest_artifact.advisor_status:
            lines.append("latest_planning_artifact_advisor_status: " + latest_artifact.advisor_status)
        if latest_artifact.advisor_risk_flags:
            lines.append(
                "latest_planning_artifact_risk_flags: "
                + ", ".join(latest_artifact.advisor_risk_flags)
            )
        lines.append("latest_planning_artifact_task_count: " + str(len(latest_artifact.task_ids)))
        lines.append(
            "latest_planning_artifact_supersedes: "
            + str(latest_artifact.supersedes_artifact_id or "none")
        )
        lines.append(
            "latest_planning_artifact_superseded_by: "
            + str(latest_artifact.superseded_by_artifact_id or "none")
        )
    if state.enabled_plugin_names:
        lines.append("manually_enabled_plugins: " + ", ".join(state.enabled_plugin_names))
    if state.disabled_plugin_names:
        lines.append("manually_disabled_plugins: " + ", ".join(state.disabled_plugin_names))
    if state.enabled_skill_names:
        lines.append("manually_enabled_skills: " + ", ".join(state.enabled_skill_names))
    if state.disabled_skill_names:
        lines.append("manually_disabled_skills: " + ", ".join(state.disabled_skill_names))
    first_user = _first_user_message_excerpt(state.messages)
    if first_user:
        lines.append(f"first_user_message: {first_user}")
    return "\n".join(lines)


def _resolve_transcript_path(cwd: Path, target: str) -> Path | None:
    loaded_state, loaded_path = load_transcript_by_session_id(cwd, target)
    if loaded_state is not None and loaded_path is not None:
        return loaded_path
    summaries = list_transcripts(cwd)
    matches = [item.path for item in summaries if item.session_id.startswith(target)]
    if not matches:
        matches = [item.path for item in summaries if target in item.session_id]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    return matches[0]


def _collect_tool_counts(messages: list[dict]) -> Counter[str]:
    tool_counts: Counter[str] = Counter()
    for message in messages:
        for block in list(message.get("content", []) or []):
            if block.get("type") == "tool_use":
                tool_counts[str(block.get("name", "unknown"))] += 1
    return tool_counts


def _first_user_message_excerpt(messages: list[dict]) -> str:
    for message in messages:
        if message.get("role") != "user":
            continue
        text_parts = []
        for block in list(message.get("content", []) or []):
            if block.get("type") == "text" and str(block.get("text", "")).strip():
                text_parts.append(str(block.get("text", "")).strip())
        if text_parts:
            text = " ".join(text_parts)
            return text if len(text) <= 120 else text[:117] + "..."
    return ""


def _format_counter(counter: Counter[str], *, limit: int | None = None) -> str:
    if not counter:
        return "(none)"
    items = counter.most_common(limit)
    return ", ".join(f"{name}={count}" for name, count in items)


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..session import Session
