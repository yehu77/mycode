from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
import shutil
from collections import Counter

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


def handle_plan_command(session: "Session", args: str) -> str:
    raw = args.strip()
    if not raw:
        return session.describe_active_plan()
    if raw == "list":
        return session.describe_planning_artifacts()
    if raw == "clear":
        return session.clear_active_plan()
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
    return "Usage: /plan [list|show [id|latest]|use <id>|revert <id>|clear|derive [goal]]"


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
