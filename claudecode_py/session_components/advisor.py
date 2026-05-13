from __future__ import annotations

from typing import Any

from ..providers import build_provider


class AdvisorSessionComponent:
    def __init__(self, session: Any) -> None:
        self._session = session

    def _advisor_context_lines(self, *, exclude_latest_assistant: bool) -> list[str]:
        session = self._session
        lines: list[str] = []
        if session.state.context_summary:
            summary = session.state.context_summary.strip()
            if len(summary) > 1200:
                summary = summary[:1197] + "..."
            lines.append("context_summary: " + summary.replace("\n", " | "))
        messages = (
            session.state.messages[:-1]
            if exclude_latest_assistant and session.state.messages
            else session.state.messages
        )
        for message in messages[-8:]:
            role = str(message.get("role", "unknown"))
            lines.append(f"{role}: {session._summarize_message(message)}")
        return lines

    def _render_active_plan_for_advisor(self, artifact: Any) -> str:
        session = self._session
        lines = [
            f"artifact_id: {artifact.artifact_id}",
            f"kind: {artifact.kind}",
            f"goal: {artifact.goal}",
            "summary:",
            session._compact_multiline_text(artifact.summary, max_lines=10, max_chars=1200),
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

    def has_advisor_model(self) -> bool:
        session = self._session
        if session.state.advisor_model and session.state.advisor_mode == "off":
            self._normalize_advisor_state()
        return bool(session.state.advisor_model and session.state.advisor_mode != "off")

    def uses_interactive_advisor(self) -> bool:
        session = self._session
        if session.state.advisor_model and session.state.advisor_mode == "off":
            self._normalize_advisor_state()
        return self.has_advisor_model() and session.state.advisor_mode == "interactive-review"

    def build_advisor_provider(self):
        session = self._session
        if not self.has_advisor_model():
            return None
        return build_provider(
            provider=session.config.provider,
            model=session.state.advisor_model,
            max_tokens=min(session.config.max_tokens, 2048),
            api_key=session.config.api_key,
            base_url=session.config.base_url,
        )

    def build_advisor_review_prompt(
        self,
        *,
        checkpoint: str,
        user_prompt: str,
        candidate_text: str,
        pending_tool_names: tuple[str, ...] = (),
        active_plan=None,
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
        advisor_review,
        pending_tool_names: tuple[str, ...] = (),
        active_plan=None,
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

    def build_plan_drift_review_context(
        self,
        *,
        active_plan,
        candidate_text: str,
        pending_tool_names: tuple[str, ...] = (),
    ) -> str:
        session = self._session
        lines = [
            f"active_plan_goal: {active_plan.goal}",
            "candidate_work_summary:",
            session._compact_multiline_text(candidate_text.strip(), max_lines=8, max_chars=1000),
        ]
        if pending_tool_names:
            lines.append("pending_tools: " + ", ".join(pending_tool_names))
        if active_plan.advisor_risk_flags:
            lines.append("active_plan_risk_flags: " + ", ".join(active_plan.advisor_risk_flags))
        diff_lines = session._summarize_planning_summary_diff(active_plan.summary, candidate_text)
        if diff_lines:
            lines.append("active_plan_vs_candidate_diff:")
            lines.extend(diff_lines)
        return "\n".join(lines)

    def record_plan_drift_context(self, context: str) -> None:
        session = self._session
        compact = session._compact_multiline_text(context.strip(), max_lines=16, max_chars=1800)
        session.state.last_plan_drift_context = compact or None

    def describe_advisor(self) -> str:
        session = self._session
        if not session.state.advisor_model or session.state.advisor_mode == "off":
            return (
                "Advisor: not set\n"
                "Mode: off\n"
                'Use "/advisor <model>" to enable or "/advisor mode interactive-review" after a model is set.'
            )
        lines = [
            f"Advisor: {session.state.advisor_model}",
            f"Mode: {session.state.advisor_mode}",
        ]
        if session.state.advisor_mode == "final-review":
            lines.append("Status: final answers are reviewed before they are returned.")
        else:
            lines.append("Status: plan, write-risk, and final-answer checkpoints are reviewed.")
        if session.state.advisor_last_result is not None:
            lines.append(
                "Last review: "
                f"{session.state.advisor_last_result.checkpoint}/{session.state.advisor_last_result.status}"
            )
            if session.state.advisor_last_result.reason:
                lines.append("Last reason: " + session.state.advisor_last_result.reason)
            if session.state.advisor_last_result.risk_flags:
                lines.append("Risk flags: " + ", ".join(session.state.advisor_last_result.risk_flags))
        lines.append("Execution constraints: " + session.state.active_execution_constraint)
        if session.state.constraint_source:
            lines.append("Constraint source: " + session.state.constraint_source)
        if session.state.constraint_reason:
            lines.append("Constraint reason: " + session.state.constraint_reason)
        if session.state.active_execution_plan_id:
            lines.append("Active execution plan: " + session.state.active_execution_plan_id)
        if session.state.last_plan_drift_status:
            lines.append("Last plan drift status: " + session.state.last_plan_drift_status)
        if session.state.last_plan_drift_reason:
            lines.append("Last plan drift reason: " + session.state.last_plan_drift_reason)
        if session.state.last_plan_drift_context:
            lines.append("Last plan drift analysis:")
            lines.extend(
                "  " + line
                for line in session._compact_multiline_text(
                    session.state.last_plan_drift_context,
                    max_lines=10,
                    max_chars=1200,
                ).splitlines()
            )
        active_plan = session.active_planning_artifact()
        if active_plan is not None and active_plan.advisor_risk_flags:
            lines.append("Active plan risk flags: " + ", ".join(active_plan.advisor_risk_flags))
        if active_plan is not None and active_plan.derived_from_drift:
            lines.append("Active plan derived from drift: yes")
        if active_plan is not None and active_plan.derivation_reason:
            lines.append("Active plan derivation reason: " + active_plan.derivation_reason)
        return "\n".join(lines)

    def show_advisor_status(self) -> str:
        return self.describe_advisor()

    def _recent_plan_drift_summary(self) -> str | None:
        context = self._session.state.last_plan_drift_context
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

    def _normalize_advisor_state(self) -> None:
        session = self._session
        if session.state.advisor_mode not in ("off", "final-review", "interactive-review"):
            session.state.advisor_mode = "final-review" if session.state.advisor_model else "off"
        if session.state.advisor_model is None:
            session.state.advisor_mode = "off"
        elif session.state.advisor_mode == "off":
            session.state.advisor_mode = "final-review"
        if session.state.active_execution_constraint not in {"normal", "read-only"}:
            session.state.active_execution_constraint = "normal"
        if session.state.active_execution_constraint == "normal":
            session.state.constraint_source = None
            session.state.constraint_reason = None
        if session.state.plan_execution_count < 0:
            session.state.plan_execution_count = 0
        if session.state.plan_drift_count < 0:
            session.state.plan_drift_count = 0
        if len(session.state.advisor_review_history) > 20:
            session.state.advisor_review_history = session.state.advisor_review_history[-20:]
        if session.state.advisor_last_result is None and session.state.advisor_review_history:
            session.state.advisor_last_result = session.state.advisor_review_history[-1]
        if session.state.last_plan_drift_status not in {None, "revise", "block"}:
            session.state.last_plan_drift_status = None
        if session.state.last_plan_drift_context is not None and not isinstance(
            session.state.last_plan_drift_context, str
        ):
            session.state.last_plan_drift_context = str(session.state.last_plan_drift_context)
        if session.state.active_execution_plan_id and not isinstance(
            session.state.active_execution_plan_id, str
        ):
            session.state.active_execution_plan_id = str(session.state.active_execution_plan_id)
        planning_artifacts = (
            list(session.state.planning_artifact_history)
            if session.state.planning_artifact_history
            else list(session.state.recent_planning_artifacts)
        )
        if len(planning_artifacts) > 5:
            planning_artifacts = planning_artifacts[-5:]
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
        session.state.planning_artifact_history = list(planning_artifacts)
        session.state.recent_planning_artifacts = list(planning_artifacts)
        if session.state.active_planning_artifact_id and not any(
            item.artifact_id == session.state.active_planning_artifact_id
            for item in planning_artifacts
        ):
            session.state.active_planning_artifact_id = (
                planning_artifacts[-1].artifact_id if planning_artifacts else None
            )
        elif session.state.active_planning_artifact_id is None and planning_artifacts:
            session.state.active_planning_artifact_id = planning_artifacts[-1].artifact_id
