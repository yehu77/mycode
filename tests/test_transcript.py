from pathlib import Path
import sys
import unittest
import shutil

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.state import (
    AdvisorReviewSummary,
    PlanningArtifact,
    SessionState,
    WorkspaceChangeSet,
    WorkspaceFileChange,
)
from claudecode_py.storage.transcript import (
    get_session_path,
    list_transcripts,
    load_latest_transcript,
    load_transcript_by_session_id,
    save_transcript,
)


class TranscriptTests(unittest.TestCase):
    def test_save_and_load_latest_transcript(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_transcript"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            config = SessionConfig(cwd=cwd, interactive=False)
            state = SessionState(
                session_id="session-1",
                created_at="2026-01-01T00:00:00+00:00",
                context_summary="Earlier conversation summary",
                advisor_model="claude-3-opus-latest",
                enabled_skill_names=["review"],
                disabled_skill_names=["draft"],
                messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                recent_change_sets=[
                    WorkspaceChangeSet(
                        tool_name="write_file",
                        summary="Created demo.txt",
                        files=[
                            WorkspaceFileChange(
                                path="demo.txt",
                                existed_before=False,
                                before_content="",
                                after_content="hello",
                            )
                        ],
                    )
                ],
                undone_change_sets=[
                    WorkspaceChangeSet(
                        tool_name="edit_file",
                        summary="Updated demo.txt",
                        files=[
                            WorkspaceFileChange(
                                path="demo.txt",
                                existed_before=True,
                                before_content="hello",
                                after_content="HELLO",
                            )
                        ],
                    )
                ],
            )

            path = save_transcript(config, state)
            self.assertEqual(path, get_session_path(cwd, "session-1"))
            self.assertTrue(path.exists())

            loaded_state, loaded_path = load_latest_transcript(cwd)
            self.assertIsNotNone(loaded_state)
            assert loaded_state is not None
            self.assertEqual(loaded_state.session_id, "session-1")
            self.assertEqual(loaded_state.context_summary, "Earlier conversation summary")
            self.assertEqual(loaded_state.advisor_model, "claude-3-opus-latest")
            self.assertEqual(loaded_state.enabled_skill_names, ["review"])
            self.assertEqual(loaded_state.disabled_skill_names, ["draft"])
            self.assertEqual(loaded_state.messages[0]["role"], "user")
            self.assertEqual(len(loaded_state.recent_change_sets), 1)
            self.assertEqual(len(loaded_state.undone_change_sets), 1)
            self.assertEqual(loaded_path, path)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_list_transcripts_and_load_by_session_id(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_transcript_list"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            config = SessionConfig(cwd=cwd, interactive=False)
            state_a = SessionState(
                session_id="session-a",
                created_at="2026-01-01T00:00:00+00:00",
                messages=[{"role": "user", "content": [{"type": "text", "text": "a"}]}],
            )
            state_b = SessionState(
                session_id="session-b",
                created_at="2026-01-02T00:00:00+00:00",
                context_summary="summary",
                messages=[
                    {"role": "user", "content": [{"type": "text", "text": "b1"}]},
                    {"role": "assistant", "content": [{"type": "text", "text": "b2"}]},
                ],
            )
            save_transcript(config, state_a)
            save_transcript(config, state_b)

            summaries = list_transcripts(cwd)
            self.assertEqual(len(summaries), 2)
            self.assertEqual(summaries[0].session_id, "session-b")
            self.assertEqual(summaries[0].message_count, 2)
            self.assertTrue(summaries[0].context_summary_present)

            loaded_state, loaded_path = load_transcript_by_session_id(cwd, "session-a")
            self.assertIsNotNone(loaded_state)
            self.assertEqual(loaded_state.session_id, "session-a")
            self.assertEqual(loaded_path, get_session_path(cwd, "session-a"))
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_save_and_load_advisor_and_planning_state(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_transcript_advisor_planning"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            config = SessionConfig(cwd=cwd, interactive=False)
            state = SessionState(
                session_id="session-plan",
                original_cwd=str(cwd),
                effective_cwd=str(cwd / ".pyclaude" / "worktrees" / "agent-demo"),
                workspace_mode="worktree",
                advisor_model="claude-3-opus-latest",
                advisor_mode="interactive-review",
                advisor_last_result=AdvisorReviewSummary(
                    checkpoint="before_write",
                    status="block",
                    reason="unsafe",
                    suggested_changes=["revise"],
                    risk_flags=["unsafe-write"],
                    model="claude-3-opus-latest",
                ),
                advisor_review_history=[
                    AdvisorReviewSummary(
                        checkpoint="initial_plan",
                        status="revise",
                        reason="tighten",
                        suggested_changes=["clarify"],
                        risk_flags=[],
                        model="claude-3-opus-latest",
                    )
                ],
                active_execution_constraint="read-only",
                constraint_source="before_write_block",
                constraint_reason="unsafe",
                constraint_trigger_count=2,
                active_execution_plan_id="plan-1",
                plan_execution_count=3,
                plan_drift_count=1,
                last_plan_drift_status="block",
                last_plan_drift_reason="drifted away from the active plan",
                last_plan_drift_context=(
                    "active_plan_goal: map runtime\n"
                    "pending_tools: write_file\n"
                    "active_plan_vs_candidate_diff:\n"
                    "  - revise query loop"
                ),
                recent_planning_artifacts=[
                    PlanningArtifact(
                        kind="ultraplan",
                        goal="map runtime",
                        summary="summary",
                        supersedes_artifact_id="plan-0",
                        superseded_by_artifact_id="plan-2",
                        derived_from_drift=True,
                        derivation_reason="Need a narrower runtime-only revision.",
                        used_read_only_subagents=True,
                        scout_categories=["architecture-boundaries"],
                        task_ids=["task-1"],
                        advisor_status="block",
                        advisor_risk_flags=["unsafe-write"],
                    )
                ],
                messages=[{"role": "user", "content": [{"type": "text", "text": "plan"}]}],
            )
            state.active_planning_artifact_id = state.recent_planning_artifacts[0].artifact_id

            save_transcript(config, state)
            loaded_state, _ = load_latest_transcript(cwd)

            self.assertIsNotNone(loaded_state)
            assert loaded_state is not None
            self.assertEqual(loaded_state.advisor_mode, "interactive-review")
            self.assertEqual(loaded_state.advisor_last_result.status, "block")
            self.assertEqual(loaded_state.advisor_review_history[0].checkpoint, "initial_plan")
            self.assertEqual(loaded_state.workspace_mode, "worktree")
            self.assertEqual(loaded_state.original_cwd, str(cwd))
            self.assertEqual(loaded_state.effective_cwd, str(cwd / ".pyclaude" / "worktrees" / "agent-demo"))
            self.assertEqual(loaded_state.active_execution_constraint, "read-only")
            self.assertEqual(loaded_state.constraint_source, "before_write_block")
            self.assertEqual(loaded_state.constraint_reason, "unsafe")
            self.assertEqual(loaded_state.constraint_trigger_count, 2)
            self.assertEqual(loaded_state.active_execution_plan_id, "plan-1")
            self.assertEqual(loaded_state.plan_execution_count, 3)
            self.assertEqual(loaded_state.plan_drift_count, 1)
            self.assertEqual(loaded_state.last_plan_drift_status, "block")
            self.assertEqual(loaded_state.last_plan_drift_reason, "drifted away from the active plan")
            self.assertEqual(loaded_state.last_plan_drift_context, state.last_plan_drift_context)
            self.assertEqual(loaded_state.recent_planning_artifacts[0].kind, "ultraplan")
            self.assertEqual(loaded_state.recent_planning_artifacts[0].supersedes_artifact_id, "plan-0")
            self.assertEqual(loaded_state.recent_planning_artifacts[0].superseded_by_artifact_id, "plan-2")
            self.assertTrue(loaded_state.recent_planning_artifacts[0].derived_from_drift)
            self.assertEqual(
                loaded_state.recent_planning_artifacts[0].derivation_reason,
                "Need a narrower runtime-only revision.",
            )
            self.assertTrue(loaded_state.recent_planning_artifacts[0].used_read_only_subagents)
            self.assertEqual(
                loaded_state.active_planning_artifact_id,
                loaded_state.recent_planning_artifacts[0].artifact_id,
            )
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
