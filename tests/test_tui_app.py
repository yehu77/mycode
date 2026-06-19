from pathlib import Path
from importlib.util import find_spec
import shutil
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.commands import CommandExecution
from claudecode_py.config import SessionConfig
from claudecode_py.runtime.events import RuntimeEvent
from claudecode_py.session import Session
from claudecode_py.state import SessionState
from claudecode_py.storage.background_sessions import create_background_session, update_background_session
from claudecode_py.storage.transcript import save_transcript

if find_spec("textual") is None:
    raise unittest.SkipTest("textual is not installed")

from claudecode_py.tui.app import PyClaudeTui


class TuiAppTests(unittest.TestCase):
    def test_on_mount_adds_execution_summary_for_constrained_session(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_mount_execution_summary"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        policy = session._compile_turn_command_policy(  # type: ignore[attr-defined]
            allowed_tool_names=("read_file",),
            allowed_bash_command_prefixes=("git diff",),
            require_read_only_subagents=True,
            command_policy_name="read-only-subagent",
            command_policy_source="session_execution_contract",
        )
        assert policy is not None
        session.set_session_execution_contract(
            execution_mode="read-only-subagent",
            command_policy=policy,
            active_execution_constraint="read-only",
            constraint_source="session_execution_contract",
            constraint_reason="read-only child contract",
        )
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        app._append_chat = lambda role, content: app.state.append_message(role, content)  # type: ignore[method-assign]
        app._append_event = lambda content: app.state.append_event(content)  # type: ignore[method-assign]

        try:
            app.on_mount()
            self.assertIn("execution: execution=read-only-subagent  policy=read-only-subagent  read_only_subagents=yes", app.state.events)
            self.assertIn('Type "/help" for commands.', app.state.events)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_plan_view_timeline_switches_plan_panel(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_plan_timeline"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]

        try:
            app.action_plan_view_timeline()
            self.assertEqual(app.state.plan_panel_view, "timeline")
            app.action_cycle_plan_timeline_filter()
            self.assertEqual(app.state.plan_timeline_filter, "plan")
            app.action_cycle_plan_timeline_delta_mode()
            self.assertEqual(app.state.plan_timeline_delta_mode, "before-drift")
            app.action_cycle_plan_timeline_delta_mode()
            self.assertEqual(app.state.plan_timeline_delta_mode, "after-drift")
            app.action_cycle_plan_timeline_focus_mode()
            self.assertEqual(app.state.plan_timeline_focus_mode, "scout")
            app.action_cycle_plan_timeline_compare_mode()
            self.assertEqual(app.state.plan_timeline_compare_mode, "after-drift-vs-all")
            app.action_select_next_timeline_compare()
            self.assertEqual(app.state.selected_plan_timeline_compare_index, 1)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_plan_view_audit_switches_panel_and_runs_selected_actions(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_plan_audit"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        app._plan_panel_text = lambda: (  # type: ignore[method-assign]
            "selected_audit_artifact_id: plan-123\n"
            "selected_audit_primary_action: /planning replay latest artifact=plan-123\n"
            "selected_audit_secondary_action: /planning timeline all artifact=plan-123\n"
        )

        try:
            app.action_plan_view_audit()
            self.assertEqual(app.state.plan_panel_view, "audit")
            with patch("claudecode_py.tui.app._handle_repl_command", return_value=(True, "audit replay")):
                app.action_execute_lineage_show()
            self.assertIn("[You]\n/planning replay latest artifact=plan-123", app.state.messages)
            self.assertIn("[System]\naudit replay", app.state.messages)
            with patch("claudecode_py.tui.app._handle_repl_command", return_value=(True, "audit timeline")):
                app.action_execute_lineage_default_action()
            self.assertIn("[You]\n/planning timeline all artifact=plan-123", app.state.messages)
            self.assertIn("[System]\naudit timeline", app.state.messages)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_plan_view_replay_uses_timeline_context_and_navigation(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_plan_replay"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app.state.set_plan_panel_view("timeline")
        app.state.plan_timeline_compare_mode = "after-drift-vs-all"
        app.state.selected_plan_timeline_index = 2
        app._render = lambda: None  # type: ignore[method-assign]

        def render_text() -> str:
            if app.state.plan_panel_view == "timeline":
                return (
                    "selected_timeline_compare_primary_action: /planning advisor\n"
                    "selected_timeline_compare_secondary_action: /task drift task-123\n"
                )
            if app.state.selected_plan_replay_index == 2:
                return (
                    "selected_replay_primary_action: /task show task-123\n"
                    "selected_replay_secondary_action: /task drift task-123\n"
                )
            return (
                "selected_replay_primary_action: /planning advisor\n"
                "selected_replay_secondary_action: /planning execution\n"
            )

        app._plan_panel_text = render_text  # type: ignore[method-assign]

        try:
            app.action_plan_view_replay()
            self.assertEqual(app.state.plan_panel_view, "replay")
            self.assertEqual(app.state.plan_replay_source_mode, "compare-item")
            self.assertEqual(app.state.selected_plan_replay_index, 2)

            app.action_select_prev_plan_lineage()
            self.assertEqual(app.state.selected_plan_replay_index, 1)
            app.action_select_next_plan_lineage()
            self.assertEqual(app.state.selected_plan_replay_index, 2)

            with patch("claudecode_py.tui.app._handle_repl_command", return_value=(True, "task detail")):
                app.action_execute_lineage_show()
                self.assertIn("[System]\ntask detail", app.state.messages[-1])
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_plan_view_replay_preserves_phase_local_slice_context(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_plan_replay_phase_local"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app.state.set_plan_panel_view("timeline")
        app._render = lambda: None  # type: ignore[method-assign]
        app._plan_panel_text = lambda: (  # type: ignore[method-assign]
            "selected_phase_local_task_id: task-123\n"
            "selected_phase_local_task_action: /task show task-123\n"
            "recent_drift_linked_task: task-123\n"
            "recent_drift_linked_task_action: /task drift task-123\n"
        )

        try:
            app.action_plan_view_replay()
            self.assertEqual(app.state.plan_panel_view, "replay")
            self.assertEqual(app.state.plan_replay_source_mode, "phase-local-summary")
            self.assertEqual(app.state.plan_replay_phase_filter, "execution-loop")
            self.assertIsNone(app.state.plan_replay_artifact_id)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_plan_view_replay_preserves_compare_artifact_slice_context(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_plan_replay_compare_slice"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app.state.set_plan_panel_view("timeline")
        app.state.plan_timeline_compare_mode = "active-vs-previous"
        app._render = lambda: None  # type: ignore[method-assign]
        app._plan_panel_text = lambda: (  # type: ignore[method-assign]
            "selected_timeline_compare_label: phase:Execution Loop\n"
            "selected_timeline_compare_primary_action: /planning timeline all phase=execution-loop artifact=artifact-active\n"
            "selected_timeline_compare_secondary_action: /planning timeline all phase=execution-loop artifact=artifact-prev\n"
        )

        try:
            app.action_plan_view_replay()
            self.assertEqual(app.state.plan_panel_view, "replay")
            self.assertEqual(app.state.plan_replay_source_mode, "compare-item")
            self.assertEqual(app.state.plan_replay_phase_filter, "execution-loop")
            self.assertEqual(app.state.plan_replay_artifact_id, "artifact-active")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_timeline_and_audit_views_expose_file_context_metadata_for_footer_hints(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_plan_timeline_file_context"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        session.task_file_context_payload = lambda task_id: {  # type: ignore[method-assign]
            "file_context_scope": "task",
            "file_context_file_count": 1,
            "file_context_files": [
                {
                    "path": "runtime/session.py",
                    "target": {
                        "action": "open_file",
                        "path": "runtime/session.py",
                        "line": 18,
                        "label": "timeline task file",
                    },
                }
            ],
        }
        session.describe_active_plan_timeline_at = (  # type: ignore[method-assign]
            lambda *args, **kwargs: (
                "timeline_artifact: plan-1\n"
                "selected_timeline_task_id: task-123\n"
                "selected_timeline_primary_action: /task show task-123 file 1\n"
                "selected_timeline_secondary_action: /task drift task-123"
            )
        )
        session.describe_active_plan_audit = (  # type: ignore[method-assign]
            lambda *args, **kwargs: (
                "selected_audit_artifact_id: plan-1\n"
                "selected_audit_primary_action: /planning replay latest artifact=plan-1\n"
                "selected_audit_secondary_action: /planning timeline all artifact=plan-1"
            )
        )
        session.active_plan_file_context_payload = lambda identifier=None: {  # type: ignore[method-assign]
            "file_context_scope": "active_plan",
            "file_context_file_count": 1,
            "file_context_files": [
                {
                    "path": "runtime/query_loop.py",
                    "target": {
                        "action": "open_file",
                        "path": "runtime/query_loop.py",
                        "line": 27,
                        "label": "audit plan file",
                    },
                }
            ],
        }

        try:
            app.state.set_plan_panel_view("timeline")
            timeline_context = app._selected_file_context_context()
            self.assertIsNotNone(timeline_context)
            assert timeline_context is not None
            self.assertEqual(timeline_context["source"], "task")
            app._update_file_context_footer_hints()
            left_binding = app._bindings.key_to_bindings["ctrl+left"][0]
            right_binding = app._bindings.key_to_bindings["ctrl+right"][0]
            self.assertEqual(left_binding.description, "Prev (focus task files)")
            self.assertEqual(right_binding.description, "Next (focus task files)")

            app.state.set_plan_panel_view("audit")
            audit_context = app._selected_file_context_context()
            self.assertIsNotNone(audit_context)
            assert audit_context is not None
            self.assertEqual(audit_context["source"], "plan")
            app._update_file_context_footer_hints()
            left_binding = app._bindings.key_to_bindings["ctrl+left"][0]
            right_binding = app._bindings.key_to_bindings["ctrl+right"][0]
            self.assertEqual(left_binding.description, "Prev (focus plan files)")
            self.assertEqual(right_binding.description, "Next (focus plan files)")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_timeline_navigation_actions_run_selected_entry_commands(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_plan_timeline_nav"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app.state.set_plan_panel_view("timeline")
        app._render = lambda: None  # type: ignore[method-assign]
        app._plan_panel_text = lambda: (  # type: ignore[method-assign]
            "selected_timeline_primary_action: /task show task-123\n"
            "selected_timeline_secondary_action: /task drift task-123\n"
        )

        try:
            with patch("claudecode_py.tui.app._handle_repl_command", return_value=(True, "task detail")):
                app.action_execute_lineage_show()
            self.assertIn("[You]\n/task show task-123", app.state.messages)
            self.assertIn("[System]\ntask detail", app.state.messages)

            with patch("claudecode_py.tui.app._handle_repl_command", return_value=(True, "drift detail")):
                app.action_execute_lineage_default_action()
            self.assertIn("[You]\n/task drift task-123", app.state.messages)
            self.assertIn("[System]\ndrift detail", app.state.messages)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_timeline_compare_navigation_actions_run_selected_compare_commands(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_plan_timeline_compare_nav"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app.state.set_plan_panel_view("timeline")
        app._render = lambda: None  # type: ignore[method-assign]
        app._plan_panel_text = lambda: (  # type: ignore[method-assign]
            "selected_timeline_compare_primary_action: /planning advisor\n"
            "selected_timeline_compare_secondary_action: /task drift task-123\n"
        )

        try:
            with patch("claudecode_py.tui.app._handle_repl_command", return_value=(True, "advisor detail")):
                app.action_open_execution_plan_advisor()
            self.assertIn("[You]\n/planning advisor", app.state.messages)
            self.assertIn("[System]\nadvisor detail", app.state.messages)

            with patch("claudecode_py.tui.app._handle_repl_command", return_value=(True, "drift detail")):
                app.action_show_execution_advisor_status()
            self.assertIn("[You]\n/task drift task-123", app.state.messages)
            self.assertIn("[System]\ndrift detail", app.state.messages)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_timeline_phase_local_summary_actions_open_task_and_drift_detail(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_plan_timeline_phase_local"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app.state.set_plan_panel_view("timeline")
        app._render = lambda: None  # type: ignore[method-assign]
        app._plan_panel_text = lambda: (  # type: ignore[method-assign]
            "selected_phase_local_task_id: task-123\n"
            "selected_phase_local_task_action: /task show task-123\n"
            "recent_drift_linked_task: task-123\n"
            "recent_drift_linked_task_action: /task drift task-123\n"
        )
        session.describe_task_detail = lambda task_id: f"task detail for {task_id}"  # type: ignore[method-assign]
        session.open_task_drift_detail = lambda task_id: f"drift detail for {task_id}"  # type: ignore[method-assign]

        try:
            app.action_open_selected_plan_task()
            self.assertEqual(app.state.selected_task_id, "task-123")
            self.assertEqual(app.state.task_detail_text, "task detail for task-123")

            app.action_show_execution_advisor_status()
            self.assertEqual(app.state.selected_task_id, "task-123")
            self.assertEqual(app.state.task_detail_view, "drift")
            self.assertEqual(app.state.task_drift_text, "drift detail for task-123")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_timeline_phase_local_task_selector_switches_selected_task(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_plan_timeline_phase_selector"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app.state.set_plan_panel_view("timeline")
        app._render = lambda: None  # type: ignore[method-assign]

        def render_text() -> str:
            if app.state.selected_phase_local_task_index == 0:
                return (
                    "selected_phase_local_task_id: task-123\n"
                    "selected_phase_local_task_position: 1/2\n"
                    "selected_phase_local_task_action: /task show task-123\n"
                    "recent_drift_linked_task: task-456\n"
                    "recent_drift_linked_task_action: /task drift task-456\n"
                )
            return (
                "selected_phase_local_task_id: task-456\n"
                "selected_phase_local_task_position: 2/2\n"
                "selected_phase_local_task_action: /task show task-456\n"
                "recent_drift_linked_task: task-456\n"
                "recent_drift_linked_task_action: /task drift task-456\n"
            )

        app._plan_panel_text = render_text  # type: ignore[method-assign]
        session.describe_task_detail = lambda task_id: f"task detail for {task_id}"  # type: ignore[method-assign]
        session.open_task_drift_detail = lambda task_id: f"drift detail for {task_id}"  # type: ignore[method-assign]

        try:
            app.action_open_selected_plan_task()
            self.assertEqual(app.state.selected_task_id, "task-123")
            self.assertEqual(app.state.task_detail_text, "task detail for task-123")

            app.action_select_next_phase_local_task()
            app.action_open_selected_plan_task()
            self.assertEqual(app.state.selected_task_id, "task-456")
            self.assertEqual(app.state.task_detail_text, "task detail for task-456")

            app.action_show_execution_advisor_status()
            self.assertEqual(app.state.selected_task_id, "task-456")
            self.assertEqual(app.state.task_detail_view, "drift")
            self.assertEqual(app.state.task_drift_text, "drift detail for task-456")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_toggle_plan_timeline_focus_selected_task_uses_current_task_context(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_timeline_task_focus"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app.state.set_plan_panel_view("execution")
        app._render = lambda: None  # type: ignore[method-assign]
        app._plan_panel_text = lambda: (  # type: ignore[method-assign]
            "selected_execution_task_id: task-123\n"
            "selected_execution_task_action: /task show task-123\n"
        )

        try:
            app.action_toggle_plan_timeline_focus_selected_task()
            self.assertEqual(app.state.plan_panel_view, "timeline")
            self.assertEqual(app.state.plan_timeline_focus_mode, "task:task-123")

            app.action_toggle_plan_timeline_focus_selected_task()
            self.assertEqual(app.state.plan_timeline_focus_mode, "none")

            app.state.selected_task_id = "task-456"
            app.action_toggle_plan_timeline_focus_selected_task()
            self.assertEqual(app.state.plan_timeline_focus_mode, "task:task-456")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_open_selected_plan_task_uses_shared_task_detail_action_for_execution(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_execution_task"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app.state.set_plan_panel_view("execution")
        app._render = lambda: None  # type: ignore[method-assign]
        app._plan_panel_text = lambda: (  # type: ignore[method-assign]
            "selected_execution_task_id: task-123\n"
            "selected_execution_task_action: /task show task-123\n"
        )
        session.describe_task_detail = lambda task_id: f"task detail for {task_id}"  # type: ignore[method-assign]

        try:
            app.action_open_selected_plan_task()
            self.assertEqual(app.state.selected_task_id, "task-123")
            self.assertEqual(app.state.task_detail_text, "task detail for task-123")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_open_selected_plan_task_uses_shared_task_detail_action_for_scout(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_scout_task"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app.state.set_plan_panel_view("scouts")
        app._render = lambda: None  # type: ignore[method-assign]
        app._plan_panel_text = lambda: (  # type: ignore[method-assign]
            "selected_scout_task_id: task-abc\n"
            "selected_scout_task_action: /task show task-abc\n"
        )
        session.describe_task_detail = lambda task_id: f"task detail for {task_id}"  # type: ignore[method-assign]

        try:
            app.action_open_selected_plan_task()
            self.assertEqual(app.state.selected_task_id, "task-abc")
            self.assertEqual(app.state.task_detail_text, "task detail for task-abc")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_open_execution_plan_advisor_switches_plan_panel(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_execution_advisor"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app.state.set_plan_panel_view("execution")
        app.state.plan_file_context_index = 1
        app._render = lambda: None  # type: ignore[method-assign]
        session.describe_active_plan_execution_at = (  # type: ignore[method-assign]
            lambda *args, **kwargs: "selected_execution_task_id: task-123\nselected_execution_task_action: /task show task-123"
        )
        session.open_task_detail_advisor = lambda task_id: f"advisor detail for {task_id}"  # type: ignore[method-assign]
        session.task_file_context_payload = lambda task_id: {  # type: ignore[method-assign]
            "file_context_scope": "task",
            "file_context_file_count": 2,
            "file_context_files": [{"path": "a.py"}, {"path": "b.py"}],
        }
        session.preferred_task_file_index = lambda task_id, fallback=0: 1  # type: ignore[method-assign]

        try:
            app.action_open_execution_plan_advisor()
            self.assertEqual(app.state.plan_panel_view, "advisor")
            self.assertEqual(app.state.task_detail_view, "advisor")
            self.assertEqual(app.state.task_advisor_text, "advisor detail for task-123")
            self.assertEqual(app.state.task_detail_file_context_index, 1)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_show_execution_advisor_status_runs_command_immediately(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_execution_status"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app.state.set_plan_panel_view("execution")
        app._render = lambda: None  # type: ignore[method-assign]
        session.show_advisor_status = lambda: "Advisor: advisor-model"  # type: ignore[method-assign]

        try:
            app.action_show_execution_advisor_status()

            self.assertIn("[System]\nAdvisor: advisor-model", app.state.messages)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_task_detail_can_jump_to_linked_advisor_and_drift(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_task_detail_nav"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        app.state.selected_task_id = "task-123"
        app.state.task_detail_text = "task_id: task-123\nmetadata:\n- task_role: execution"
        session.open_task_detail_advisor = lambda task_id: f"advisor detail for {task_id}"  # type: ignore[method-assign]
        session.open_task_drift_detail = lambda task_id: f"drift detail for {task_id}"  # type: ignore[method-assign]

        try:
            app.action_open_execution_plan_advisor()
            self.assertEqual(app.state.plan_panel_view, "advisor")
            self.assertEqual(app.state.task_detail_view, "advisor")
            self.assertEqual(app.state.task_advisor_text, "advisor detail for task-123")

            app.action_show_execution_advisor_status()
            self.assertEqual(app.state.selected_task_id, "task-123")
            self.assertEqual(app.state.task_detail_view, "drift")
            self.assertEqual(app.state.task_drift_text, "drift detail for task-123")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_task_detail_panel_subview_actions_switch_between_detail_advisor_and_drift(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_task_detail_views"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        app.state.selected_task_id = "task-123"
        app.state.task_detail_text = "task_id: task-123\nmetadata:\n- task_role: execution"
        session.open_task_detail_advisor = lambda task_id: f"advisor detail for {task_id}"  # type: ignore[method-assign]
        session.open_task_drift_detail = lambda task_id: f"drift detail for {task_id}"  # type: ignore[method-assign]

        try:
            app.action_task_view_advisor()
            self.assertEqual(app.state.task_detail_view, "advisor")
            self.assertEqual(app.state.task_advisor_text, "advisor detail for task-123")

            app.action_task_view_drift()
            self.assertEqual(app.state.task_detail_view, "drift")
            self.assertEqual(app.state.task_drift_text, "drift detail for task-123")

            app.action_task_view_detail()
            self.assertEqual(app.state.task_detail_view, "detail")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_workspace_primary_action_uses_selected_task_detail_context(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_workspace_primary"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        app.state.selected_task_id = "task-123"
        session.task_workspace_action_bundle = lambda task_id: {  # type: ignore[method-assign]
            "primary_action": "workspace_repair missing-agent",
            "secondary_action": "workspace_cleanup_preview",
            "tertiary_action": "/workspaces list",
            "target": "missing-agent",
            "workspace_health": "unavailable",
        }
        session.task_workspace_detail_metadata = lambda task_id: {  # type: ignore[method-assign]
            "workspace_action": "repair",
            "workspace_target": "missing-agent",
            "workspace_health_before": "unavailable",
            "workspace_health_after": "cleanup_pending",
            "workspace_planned_paths": ["C:/tmp/missing-agent"],
            "workspace_applied_paths": ["C:/tmp/missing-agent"],
            "workspace_failure_reason": None,
        }
        seen: list[str] = []
        session.workspace_repair = lambda arg: seen.append(arg) or f"repaired {arg}"  # type: ignore[method-assign]
        session.describe_task_detail = lambda task_id: f"task detail refreshed for {task_id}"  # type: ignore[method-assign]

        try:
            app.action_execute_workspace_primary_action()

            self.assertEqual(seen, ["missing-agent"])
            self.assertIn("[You]\nworkspace_repair missing-agent", app.state.messages)
            self.assertIn("[System]\nrepaired missing-agent", app.state.messages)
            self.assertEqual(app.state.task_detail_text, "task detail refreshed for task-123")
            self.assertEqual(
                app.state.task_detail_workspace_metadata,
                {
                    "workspace_action": "repair",
                    "workspace_target": "missing-agent",
                    "workspace_health_before": "unavailable",
                    "workspace_health_after": "cleanup_pending",
                    "workspace_planned_paths": ["C:/tmp/missing-agent"],
                    "workspace_applied_paths": ["C:/tmp/missing-agent"],
                    "workspace_failure_reason": None,
                },
            )
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_workspace_secondary_action_falls_back_to_config_context(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_workspace_secondary"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        session.current_workspace_action_bundle = lambda: {  # type: ignore[method-assign]
            "primary_action": "workspace_repair workspace-session",
            "secondary_action": "workspace_cleanup_preview",
            "tertiary_action": "/workspaces list",
            "target": "workspace-session",
            "workspace_health": "unavailable",
        }
        session.workspace_cleanup_preview = lambda: "cleanup preview"  # type: ignore[method-assign]

        try:
            app.action_execute_workspace_secondary_action()

            self.assertIn("[You]\nworkspace_cleanup_preview", app.state.messages)
            self.assertIn("[System]\ncleanup preview", app.state.messages)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_symbol_primary_and_secondary_actions_use_current_symbol_surface(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_symbol_actions"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "def build():\n"
            "    return 1\n\n"
            "value = build()\n",
            encoding="utf-8",
        )
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]

        try:
            handled, output = session.handle_repl_command("/symbol actions build")
            self.assertTrue(handled)
            self.assertIn("surface_kind: symbol_actions", str(output))

            app.action_execute_symbol_primary_action()
            self.assertIn("[You]\n/symbol open primary", app.state.messages)
            self.assertIn("[System]\nSelected symbol navigation target: open_symbol demo.py:1", app.state.messages[-1])

            app.action_execute_symbol_secondary_action()
            self.assertIn("[You]\n/symbol open secondary", app.state.messages)
            self.assertIn("[System]\nSelected symbol navigation target: open_reference demo.py:4", app.state.messages[-1])
            metadata = app._current_symbol_surface_metadata()
            self.assertIsNotNone(metadata)
            self.assertEqual(metadata["selected_navigation_target"]["action"], "open_reference")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_symbol_selection_actions_cycle_current_surface(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_symbol_cycle"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]

        try:
            session._remember_symbol_surface(  # noqa: SLF001
                {
                    "surface_kind": "symbol_actions",
                    "selected_symbol": "build",
                    "definition_count": 2,
                    "reference_count": 2,
                    "definitions": [
                        {"action": "open_symbol", "path": "demo.py", "line": 1, "label": "definition one"},
                        {"action": "open_symbol", "path": "demo.py", "line": 10, "label": "definition two"},
                    ],
                    "references": [
                        {"action": "open_reference", "path": "demo.py", "line": 4, "label": "reference one"},
                        {"action": "open_reference", "path": "demo.py", "line": 20, "label": "reference two"},
                    ],
                    "selected_definition": {
                        "action": "open_symbol",
                        "path": "demo.py",
                        "line": 1,
                        "label": "definition one",
                    },
                    "selected_definition_index": 0,
                    "selected_reference": {
                        "action": "open_reference",
                        "path": "demo.py",
                        "line": 4,
                        "label": "reference one",
                    },
                    "selected_reference_index": 0,
                    "navigation_target": {
                        "action": "open_symbol",
                        "path": "demo.py",
                        "line": 1,
                        "label": "definition one",
                    },
                    "selected_navigation_target": {
                        "action": "open_symbol",
                        "path": "demo.py",
                        "line": 1,
                        "label": "definition one",
                    },
                }
            )

            app.action_select_next_symbol_primary_target()
            self.assertIn("[You]\n/symbol next definition", app.state.messages)
            payload = session.current_symbol_surface_payload()
            self.assertEqual(payload["selected_definition_index"], 1)
            self.assertEqual(payload["selected_navigation_target"]["line"], 10)

            app.action_select_next_symbol_reference()
            self.assertIn("[You]\n/symbol next reference", app.state.messages)
            payload = session.current_symbol_surface_payload()
            self.assertEqual(payload["selected_reference_index"], 1)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_symbol_focus_group_and_open_follow_structured_candidates(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_symbol_focus"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]

        try:
            session._remember_symbol_surface(  # noqa: SLF001
                {
                    "surface_kind": "symbol_actions",
                    "selected_symbol": "build",
                    "definition_count": 1,
                    "reference_count": 2,
                    "definitions": [
                        {"action": "open_symbol", "path": "demo.py", "line": 1, "label": "definition one"},
                    ],
                    "references": [
                        {"action": "open_reference", "path": "demo.py", "line": 4, "label": "reference one"},
                        {"action": "open_reference", "path": "demo.py", "line": 20, "label": "reference two"},
                    ],
                    "selected_definition": {
                        "action": "open_symbol",
                        "path": "demo.py",
                        "line": 1,
                        "label": "definition one",
                    },
                    "selected_definition_index": 0,
                    "selected_reference": {
                        "action": "open_reference",
                        "path": "demo.py",
                        "line": 4,
                        "label": "reference one",
                    },
                    "selected_reference_index": 0,
                    "navigation_target": {
                        "action": "open_symbol",
                        "path": "demo.py",
                        "line": 1,
                        "label": "definition one",
                    },
                    "selected_navigation_target": {
                        "action": "open_symbol",
                        "path": "demo.py",
                        "line": 1,
                        "label": "definition one",
                    },
                }
            )

            app.action_select_next_symbol_focus_group()
            self.assertEqual(app.state.symbol_focus_group, "references")
            self.assertEqual(app.state.symbol_focus_index, 0)

            app.action_select_next_symbol_focus_item()
            self.assertEqual(app.state.symbol_focus_group, "references")
            self.assertEqual(session.current_symbol_surface_payload()["selected_reference_index"], 1)

            app.action_open_focused_symbol_candidate()
            self.assertIn("[You]\n/symbol open secondary", app.state.messages)
            self.assertIn("[System]\nSelected symbol navigation target: open_reference demo.py:20", app.state.messages[-1])
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_checklist_primary_action_updates_task_and_refreshes_detail(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_checklist_primary"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        created = session.create_checklist_task(
            subject="Inspect runtime",
            description="Inspect session.py",
            active_form="Inspecting runtime",
            status="pending",
            owner="assistant",
        )
        app.state.selected_task_id = created["id"]
        app._refresh_selected_task_detail()

        try:
            app.action_execute_checklist_primary_action()

            updated = session.get_checklist_task(created["id"])
            assert updated is not None
            self.assertEqual(updated["status"], "in_progress")
            self.assertIn(f"[You]\nchecklist_mark_in_progress {created['id']}", app.state.messages)
            self.assertIn(f"[System]\nUpdated checklist task \"{created['id']}\" to in_progress", app.state.messages[-1])
            self.assertEqual(app.state.task_detail_checklist_metadata["checklist_status"], "in_progress")
            self.assertEqual(
                app.state.task_detail_checklist_metadata["selected_checklist_primary_action"],
                f"checklist_mark_completed {created['id']}",
            )
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_checklist_secondary_action_reopens_in_progress_task(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_checklist_secondary"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        created = session.create_checklist_task(
            subject="Inspect runtime",
            description="Inspect session.py",
            active_form="Inspecting runtime",
            status="in_progress",
            owner="assistant",
        )
        app.state.selected_task_id = created["id"]
        app._refresh_selected_task_detail()

        try:
            app.action_execute_checklist_secondary_action()

            updated = session.get_checklist_task(created["id"])
            assert updated is not None
            self.assertEqual(updated["status"], "pending")
            self.assertIn(f"[You]\nchecklist_reopen {created['id']}", app.state.messages)
            self.assertEqual(app.state.task_detail_checklist_metadata["checklist_status"], "pending")
            self.assertEqual(
                app.state.task_detail_checklist_metadata["selected_checklist_primary_action"],
                f"checklist_mark_in_progress {created['id']}",
            )
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_checklist_selection_and_open_detail_uses_tasks_panel_payload(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_checklist_selection"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        first = session.create_checklist_task(
            subject="Inspect runtime",
            description="Inspect session.py",
            active_form="Inspecting runtime",
            status="pending",
            owner="assistant",
        )
        second = session.create_checklist_task(
            subject="Patch runtime",
            description="Patch session.py",
            active_form="Patching runtime",
            status="in_progress",
            owner="assistant",
        )

        try:
            app.action_select_next_checklist_task()
            self.assertEqual(app.state.selected_checklist_task_id, second["id"])

            app.action_open_selected_checklist_task()
            self.assertEqual(app.state.selected_task_id, second["id"])
            self.assertEqual(app.state.task_detail_checklist_metadata["checklist_subject"], "Patch runtime")

            app.action_select_prev_checklist_task()
            self.assertEqual(app.state.selected_checklist_task_id, second["id"])
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_checklist_filter_cycles_and_selection_uses_visible_tasks(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_checklist_filter"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        pending = session.create_checklist_task(
            subject="Inspect runtime",
            description="Inspect session.py",
            active_form="Inspecting runtime",
            status="pending",
        )
        first = session.create_checklist_task(
            subject="Patch runtime",
            description="Patch session.py",
            active_form="Patching runtime",
            status="in_progress",
        )
        second = session.create_checklist_task(
            subject="Run tests",
            description="Run tests",
            active_form="Running tests",
            status="in_progress",
        )

        try:
            app.action_cycle_checklist_filter()
            self.assertEqual(app.state.checklist_filter, "in_progress")
            self.assertEqual(app.state.selected_checklist_task_id, second["id"])

            app.action_select_next_checklist_task()
            self.assertEqual(app.state.selected_checklist_task_id, first["id"])

            app.action_select_prev_checklist_task()
            self.assertEqual(app.state.selected_checklist_task_id, second["id"])

            visible_ids = [str(item.get("id")) for item in app._visible_checklist_tasks_payload()]
            self.assertEqual(visible_ids, [second["id"], first["id"]])
            self.assertNotIn(pending["id"], visible_ids)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_checklist_sort_cycles_and_visible_order_updates(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_checklist_sort"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        first = session.create_checklist_task(
            subject="Beta runtime",
            description="B",
            active_form="Doing beta",
            status="pending",
            owner="zoe",
        )
        second = session.create_checklist_task(
            subject="Alpha runtime",
            description="A",
            active_form="Doing alpha",
            status="pending",
            owner="alice",
        )

        try:
            app.action_cycle_checklist_sort()
            self.assertEqual(app.state.checklist_sort, "blocked")

            app.action_cycle_checklist_sort()
            self.assertEqual(app.state.checklist_sort, "owner")

            visible_ids = [str(item.get("id")) for item in app._visible_checklist_tasks_payload()]
            self.assertEqual(visible_ids, [second["id"], first["id"]])
            self.assertEqual(app.state.events[-1], "Checklist sort: owner")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_checklist_owner_edit_uses_prompt_input_flow(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_checklist_owner_edit"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        created = session.create_checklist_task(
            subject="Inspect runtime",
            description="Inspect session.py",
            active_form="Inspecting runtime",
            status="pending",
            owner="assistant",
        )
        app.state.selected_checklist_task_id = created["id"]
        app.action_open_selected_checklist_task()

        class _Event:
            value = ""

        try:
            app.action_edit_selected_checklist_owner()
            self.assertIsNotNone(app.state.pending_checklist_edit)
            self.assertEqual(app.state.pending_checklist_edit.field, "owner")

            _Event.value = ""
            app.on_input_submitted(_Event())

            updated = session.get_checklist_task(created["id"])
            assert updated is not None
            self.assertIsNone(updated["owner"])
            self.assertIsNone(app.state.pending_checklist_edit)
            self.assertEqual(app.state.task_detail_checklist_metadata["checklist_owner"], "none")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_checklist_active_form_edit_validates_non_empty(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_checklist_active_form_edit"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        created = session.create_checklist_task(
            subject="Inspect runtime",
            description="Inspect session.py",
            active_form="Inspecting runtime",
            status="pending",
            owner="assistant",
        )
        app.state.selected_checklist_task_id = created["id"]
        app.action_open_selected_checklist_task()

        class _Event:
            value = ""

        try:
            app.action_edit_selected_checklist_active_form()
            self.assertIsNotNone(app.state.pending_checklist_edit)
            self.assertEqual(app.state.pending_checklist_edit.field, "active_form")

            _Event.value = "   "
            app.on_input_submitted(_Event())
            self.assertIsNotNone(app.state.pending_checklist_edit)
            self.assertIn("Checklist active_form cannot be empty.", app.state.events[-1])

            _Event.value = "Reviewing runtime"
            app.on_input_submitted(_Event())
            updated = session.get_checklist_task(created["id"])
            assert updated is not None
            self.assertEqual(updated["active_form"], "Reviewing runtime")
            self.assertIsNone(app.state.pending_checklist_edit)
            self.assertEqual(
                app.state.task_detail_checklist_metadata["checklist_active_form"],
                "Reviewing runtime",
            )
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_checklist_subject_and_description_edits_use_prompt_input_flow(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_checklist_subject_description_edit"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        created = session.create_checklist_task(
            subject="Inspect runtime",
            description="Inspect session.py",
            active_form="Inspecting runtime",
            status="pending",
            owner="assistant",
        )
        app.state.selected_checklist_task_id = created["id"]
        app.action_open_selected_checklist_task()

        class _Event:
            value = ""

        try:
            app.action_edit_selected_checklist_subject()
            self.assertIsNotNone(app.state.pending_checklist_edit)
            self.assertEqual(app.state.pending_checklist_edit.field, "subject")

            _Event.value = "   "
            app.on_input_submitted(_Event())
            self.assertIsNotNone(app.state.pending_checklist_edit)
            self.assertIn("Checklist subject cannot be empty.", app.state.events[-1])

            _Event.value = "Review runtime flow"
            app.on_input_submitted(_Event())
            updated = session.get_checklist_task(created["id"])
            assert updated is not None
            self.assertEqual(updated["subject"], "Review runtime flow")
            self.assertEqual(
                app.state.task_detail_checklist_metadata["checklist_subject"],
                "Review runtime flow",
            )

            app.action_edit_selected_checklist_description()
            self.assertIsNotNone(app.state.pending_checklist_edit)
            self.assertEqual(app.state.pending_checklist_edit.field, "description")
            self.assertTrue(app.state.pending_checklist_edit.multiline)
            _Event.value = ".done"
            app.on_input_submitted(_Event())
            updated = session.get_checklist_task(created["id"])
            assert updated is not None
            self.assertEqual(updated["description"], "")
            self.assertEqual(
                app.state.task_detail_checklist_metadata["checklist_description"],
                "",
            )
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_checklist_description_and_metadata_multiline_edit_flow(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_checklist_multiline_edit"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        created = session.create_checklist_task(
            subject="Inspect runtime",
            description="Inspect session.py",
            active_form="Inspecting runtime",
            status="pending",
        )
        app.state.selected_checklist_task_id = created["id"]
        app.action_open_selected_checklist_task()

        class _Event:
            value = ""

        try:
            app.action_edit_selected_checklist_description()
            self.assertIsNotNone(app.state.pending_checklist_edit)
            self.assertTrue(app.state.pending_checklist_edit.multiline)
            _Event.value = "Line one"
            app.on_input_submitted(_Event())
            _Event.value = "Line two"
            app.on_input_submitted(_Event())
            _Event.value = ".done"
            app.on_input_submitted(_Event())

            updated = session.get_checklist_task(created["id"])
            assert updated is not None
            self.assertEqual(updated["description"], "Line one\nLine two")
            self.assertEqual(
                app.state.task_detail_checklist_metadata["checklist_description"],
                "Line one\nLine two",
            )

            app.action_edit_selected_checklist_metadata()
            self.assertIsNotNone(app.state.pending_checklist_edit)
            self.assertTrue(app.state.pending_checklist_edit.multiline)
            _Event.value = "area=runtime"
            app.on_input_submitted(_Event())
            _Event.value = "priority=high"
            app.on_input_submitted(_Event())
            _Event.value = ".done"
            app.on_input_submitted(_Event())
            updated = session.get_checklist_task(created["id"])
            assert updated is not None
            self.assertEqual(updated["metadata"], {"area": "runtime", "priority": "high"})
            self.assertEqual(
                app.state.task_detail_checklist_metadata["checklist_metadata"],
                {"area": "runtime", "priority": "high"},
            )

            app.action_edit_selected_checklist_metadata()
            _Event.value = "broken-line"
            app.on_input_submitted(_Event())
            _Event.value = ".done"
            app.on_input_submitted(_Event())
            self.assertIsNotNone(app.state.pending_checklist_edit)
            self.assertIn("Checklist metadata lines must use key=value format.", app.state.events[-1])
            _Event.value = ".cancel"
            app.on_input_submitted(_Event())
            self.assertIsNone(app.state.pending_checklist_edit)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_checklist_blocks_and_blocked_by_edits_replace_lists(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_checklist_dependency_edit"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        upstream = session.create_checklist_task(
            subject="Upstream",
            description="Task A",
            active_form="Doing upstream",
        )
        downstream = session.create_checklist_task(
            subject="Downstream",
            description="Task B",
            active_form="Doing downstream",
            blocks=[upstream["id"]],
            blocked_by=[upstream["id"]],
        )
        app.state.selected_checklist_task_id = downstream["id"]
        app.action_open_selected_checklist_task()

        class _Event:
            value = ""

        try:
            app.action_edit_selected_checklist_blocks()
            self.assertIsNotNone(app.state.pending_checklist_edit)
            self.assertEqual(app.state.pending_checklist_edit.field, "blocks")
            self.assertTrue(app.state.pending_checklist_edit.multiline)
            _Event.value = ".done"
            app.on_input_submitted(_Event())
            updated = session.get_checklist_task(downstream["id"])
            assert updated is not None
            self.assertEqual(updated["blocks"], [])
            self.assertEqual(app.state.task_detail_checklist_metadata["checklist_blocks"], [])

            app.action_edit_selected_checklist_blocked_by()
            self.assertIsNotNone(app.state.pending_checklist_edit)
            self.assertEqual(app.state.pending_checklist_edit.field, "blocked_by")
            self.assertTrue(app.state.pending_checklist_edit.multiline)
            _Event.value = f'{upstream["id"]}'
            app.on_input_submitted(_Event())
            _Event.value = "task-z"
            app.on_input_submitted(_Event())
            _Event.value = ".done"
            app.on_input_submitted(_Event())
            updated = session.get_checklist_task(downstream["id"])
            assert updated is not None
            self.assertEqual(updated["blocked_by"], [upstream["id"]])
            self.assertEqual(
                app.state.task_detail_checklist_metadata["checklist_blocked_by"],
                [upstream["id"]],
            )
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_open_selected_plan_task_stores_structured_task_detail_metadata(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_workspace_detail_metadata"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        app._selected_plan_task_context = lambda: {  # type: ignore[method-assign]
            "task_id": "task-123",
            "command": "/task show task-123",
        }
        session.describe_task_detail = lambda task_id: f"task detail for {task_id}"  # type: ignore[method-assign]
        session.task_execution_detail_metadata = lambda task_id: {  # type: ignore[method-assign]
            "task_surface": "child_execution",
            "execution_mode": "read-only-subagent",
            "execution_policy": "review",
            "execution_policy_source": "session_execution_contract",
            "allowed_tools": ["read_file"],
            "allowed_bash_prefixes": ["git diff"],
            "read_only_subagents": True,
            "workspace_mode": "snapshot",
            "workspace_health": "healthy",
        }
        session.task_workspace_detail_metadata = lambda task_id: {  # type: ignore[method-assign]
            "workspace_action": "cleanup",
            "workspace_target": "orphan-agent",
            "workspace_health_before": "orphaned",
            "workspace_health_after": "healthy",
            "workspace_planned_paths": ["C:/tmp/orphan-agent"],
            "workspace_applied_paths": ["C:/tmp/orphan-agent"],
            "workspace_failure_reason": None,
        }
        session.task_file_context_payload = lambda task_id: {  # type: ignore[method-assign]
            "file_context_scope": "task",
            "file_context_file_count": 1,
            "file_context_sources": ["checklist"],
            "file_context_files": [
                {
                    "path": "claudecode_py/session.py",
                    "source": "checklist",
                    "target_summary": "open_file claudecode_py/session.py:12",
                    "diff_target_count": 1,
                }
            ],
            "file_context_primary_path": "claudecode_py/session.py",
            "file_context_primary_target": {
                "action": "open_file",
                "path": "claudecode_py/session.py",
                "line": 12,
                "label": "task file",
            },
            "file_context_primary_diff_targets": {
                "hunks": [
                    {
                        "action": "open_diff",
                        "path": "claudecode_py/session.py",
                        "line": 20,
                        "label": "task diff",
                    }
                ]
            },
        }

        try:
            app.action_open_selected_plan_task()

            self.assertEqual(app.state.task_detail_text, "task detail for task-123")
            self.assertEqual(
                app.state.task_detail_execution_metadata,
                {
                    "task_surface": "child_execution",
                    "execution_mode": "read-only-subagent",
                    "execution_policy": "review",
                    "execution_policy_source": "session_execution_contract",
                    "allowed_tools": ["read_file"],
                    "allowed_bash_prefixes": ["git diff"],
                    "read_only_subagents": True,
                    "workspace_mode": "snapshot",
                    "workspace_health": "healthy",
                },
            )
            self.assertEqual(
                app.state.task_detail_workspace_metadata,
                {
                    "workspace_action": "cleanup",
                    "workspace_target": "orphan-agent",
                    "workspace_health_before": "orphaned",
                    "workspace_health_after": "healthy",
                    "workspace_planned_paths": ["C:/tmp/orphan-agent"],
                    "workspace_applied_paths": ["C:/tmp/orphan-agent"],
                    "workspace_failure_reason": None,
                },
            )
            self.assertEqual(
                app.state.task_detail_file_context_metadata["file_context_primary_path"],
                "claudecode_py/session.py",
            )
            rendered_tasks = app.state.render_task_panel(
                session.describe_tasks(),
                selected_task_id=app.state.selected_task_id,
                task_execution_metadata=app.state.task_detail_execution_metadata,
            )
            self.assertIn("Execution focus", rendered_tasks)
            self.assertIn("task: task-123", rendered_tasks)
            self.assertIn("execution_mode: read-only-subagent", rendered_tasks)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_execute_lineage_show_falls_back_to_task_file_context_target(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_task_file_context_nav"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        app.state.selected_task_id = "task-123"
        app.state.task_detail_view = "detail"
        app.state.task_detail_file_context_metadata = {
            "file_context_scope": "task",
            "file_context_file_count": 2,
            "file_context_sources": ["task", "task"],
            "file_context_files": [
                {
                    "path": "claudecode_py/session.py",
                    "source": "task",
                    "target": {
                        "action": "open_file",
                        "path": "claudecode_py/session.py",
                        "line": 12,
                        "label": "task file",
                    },
                    "diff_targets": {
                        "hunks": [
                            {
                                "action": "open_diff",
                                "path": "claudecode_py/session.py",
                                "line": 20,
                                "label": "task diff",
                            }
                        ]
                    },
                },
                {
                    "path": "claudecode_py/cli.py",
                    "source": "task",
                    "target": {
                        "action": "open_file",
                        "path": "claudecode_py/cli.py",
                        "line": 44,
                        "label": "cli file",
                    },
                    "diff_targets": {
                        "hunks": [
                            {
                                "action": "open_diff",
                                "path": "claudecode_py/cli.py",
                                "line": 47,
                                "label": "cli diff",
                            }
                        ]
                    },
                },
            ],
            "file_context_primary_path": "claudecode_py/session.py",
            "file_context_primary_target": {
                "action": "open_file",
                "path": "claudecode_py/session.py",
                "line": 12,
                "label": "task file",
            },
            "file_context_primary_diff_targets": {
                "hunks": [
                    {
                        "action": "open_diff",
                        "path": "claudecode_py/session.py",
                        "line": 20,
                        "label": "task diff",
                    }
                ]
            },
        }

        try:
            app.action_execute_lineage_show()
            self.assertIn(
                "Selected task file target: open_file claudecode_py/session.py:12 task file",
                app.state.events,
            )
            app.action_select_next_change_file()
            self.assertEqual(app.state.task_detail_file_context_index, 1)
            self.assertIn(
                "Task file focus: 2/2 (F9: open_file claudecode_py/cli.py:44 cli file; F10: open_diff claudecode_py/cli.py:47 cli diff)",
                app.state.events,
            )
            app.action_execute_lineage_default_action()
            self.assertIn(
                "Selected task file target: open_diff claudecode_py/cli.py:47 cli diff",
                app.state.events,
            )
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_execute_lineage_default_action_falls_back_to_change_diff_target(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_change_file_context_nav"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        app.state.plan_panel_view = "lineage"
        app._selected_lineage_context = lambda: None  # type: ignore[method-assign]
        session.selected_change_file_count = lambda **_kwargs: 2  # type: ignore[method-assign]
        session.selected_change_detail_metadata = lambda **_kwargs: {  # type: ignore[method-assign]
            "file_context_scope": "change",
            "file_context_file_count": 2,
            "file_context_sources": ["change"],
            "file_context_files": [
                {
                    "path": "demo.py",
                    "source": "selected_change",
                    "target": {
                        "action": "open_file",
                        "path": "demo.py",
                        "line": 7,
                        "label": "changed file",
                    },
                    "target_summary": "open_file demo.py:7",
                },
                {
                    "path": "alt.py",
                    "source": "selected_change",
                    "target": {
                        "action": "open_file",
                        "path": "alt.py",
                        "line": 11,
                        "label": "changed alt file",
                    },
                    "diff_targets": {
                        "hunks": [
                            {
                                "action": "open_diff",
                                "path": "alt.py",
                                "line": 14,
                                "label": "changed alt hunk",
                            }
                        ]
                    },
                    "target_summary": "open_file alt.py:11",
                },
            ],
            "file_context_primary_path": "demo.py",
            "file_context_primary_target": {
                "action": "open_file",
                "path": "demo.py",
                "line": 7,
                "label": "changed file",
            },
            "file_context_primary_diff_targets": {
                "hunks": [
                    {
                        "action": "open_diff",
                        "path": "demo.py",
                        "line": 9,
                        "label": "changed hunk",
                    }
                ]
            },
        }  # type: ignore[method-assign]

        try:
            app.action_select_next_change_file()
            self.assertIn(
                "Change file focus: 2/2 (F9: open_file alt.py:11 changed alt file; F10: open_diff alt.py:14 changed alt hunk)",
                app.state.events,
            )
            app.action_execute_lineage_default_action()
            self.assertIn(
                "Selected changes file target: open_diff alt.py:14 changed alt hunk",
                app.state.events,
            )
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_execute_lineage_show_falls_back_to_plan_summary_file_context_target(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_plan_file_context_nav"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        app.state.set_plan_panel_view("summary")
        session.active_plan_file_context_payload = lambda: {  # type: ignore[method-assign]
            "file_context_scope": "plan",
            "file_context_file_count": 2,
            "file_context_sources": ["plan", "plan"],
            "file_context_files": [
                {
                    "path": "claudecode_py/runtime/query_loop.py",
                    "source": "plan",
                    "target": {
                        "action": "open_file",
                        "path": "claudecode_py/runtime/query_loop.py",
                        "line": 33,
                        "label": "plan file",
                    },
                },
                {
                    "path": "claudecode_py/runtime/context.py",
                    "source": "plan",
                    "target": {
                        "action": "open_file",
                        "path": "claudecode_py/runtime/context.py",
                        "line": 18,
                        "label": "context file",
                    },
                    "diff_targets": {
                        "hunks": [
                            {
                                "action": "open_diff",
                                "path": "claudecode_py/runtime/context.py",
                                "line": 22,
                                "label": "context diff",
                            }
                        ]
                    },
                },
            ],
            "file_context_primary_path": "claudecode_py/runtime/query_loop.py",
            "file_context_primary_target": {
                "action": "open_file",
                "path": "claudecode_py/runtime/query_loop.py",
                "line": 33,
                "label": "plan file",
            },
            "file_context_primary_diff_targets": None,
        }

        try:
            app.action_execute_lineage_show()
            self.assertIn(
                "Selected plan file target: open_file claudecode_py/runtime/query_loop.py:33 plan file",
                app.state.events,
            )
            app.action_select_next_change_file()
            self.assertEqual(app.state.plan_file_context_index, 1)
            self.assertIn(
                "Plan file focus: 2/2 (F9: open_file claudecode_py/runtime/context.py:18 context file; F10: open_diff claudecode_py/runtime/context.py:22 context diff)",
                app.state.events,
            )
            app.action_execute_lineage_default_action()
            self.assertIn(
                "Selected plan file target: open_diff claudecode_py/runtime/context.py:22 context diff",
                app.state.events,
            )
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_file_context_footer_binding_descriptions_follow_current_surface(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_footer_file_context"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]

        try:
            app.state.selected_task_id = "task-123"
            app.state.task_detail_view = "detail"
            app.state.task_detail_file_context_metadata = {
                "file_context_scope": "task",
                "file_context_file_count": 1,
                "file_context_files": [
                    {
                        "path": "claudecode_py/session.py",
                        "target": {
                            "action": "open_file",
                            "path": "claudecode_py/session.py",
                            "line": 12,
                            "label": "task file",
                        },
                        "diff_targets": {
                            "hunks": [
                                {
                                    "action": "open_diff",
                                    "path": "claudecode_py/session.py",
                                    "line": 20,
                                    "label": "task diff",
                                }
                            ]
                        },
                    }
                ],
            }
            app._update_file_context_footer_hints()
            left_binding = app._bindings.key_to_bindings["ctrl+left"][0]
            right_binding = app._bindings.key_to_bindings["ctrl+right"][0]
            f9_binding = app._bindings.key_to_bindings["f9"][0]
            f10_binding = app._bindings.key_to_bindings["f10"][0]
            self.assertEqual(left_binding.description, "Prev (focus task files)")
            self.assertEqual(right_binding.description, "Next (focus task files)")
            self.assertEqual(f9_binding.description, "Primary (open_file claudecode_py/session.py:12 task file)")
            self.assertEqual(f10_binding.description, "Secondary (open_diff claudecode_py/session.py:20 task diff)")

            app.state.task_detail_view = "advisor"
            app._update_file_context_footer_hints()
            left_binding = app._bindings.key_to_bindings["ctrl+left"][0]
            right_binding = app._bindings.key_to_bindings["ctrl+right"][0]
            self.assertEqual(left_binding.description, "Prev (focus task files)")
            self.assertEqual(right_binding.description, "Next (focus task files)")

            app.state.selected_task_id = None
            app.state.task_detail_file_context_metadata = None
            app.state.set_plan_panel_view("execution")
            session.active_plan_execution_file_context_payload = lambda selected_index=0: {  # type: ignore[method-assign]
                "file_context_scope": "plan_execution",
                "file_context_file_count": 1,
                "file_context_files": [
                    {
                        "path": "claudecode_py/runtime/query_loop.py",
                        "target": {
                            "action": "open_file",
                            "path": "claudecode_py/runtime/query_loop.py",
                            "line": 33,
                            "label": "plan file",
                        },
                    }
                ],
            }
            app._update_file_context_footer_hints()
            left_binding = app._bindings.key_to_bindings["ctrl+left"][0]
            right_binding = app._bindings.key_to_bindings["ctrl+right"][0]
            self.assertEqual(left_binding.description, "Prev (focus plan files)")
            self.assertEqual(right_binding.description, "Next (focus plan files)")

            app.state.set_plan_panel_view("advisor")
            session.active_plan_file_context_payload = lambda: {  # type: ignore[method-assign]
                "file_context_scope": "plan",
                "file_context_file_count": 1,
                "file_context_files": [
                    {
                        "path": "claudecode_py/runtime/query_loop.py",
                        "target": {
                            "action": "open_file",
                            "path": "claudecode_py/runtime/query_loop.py",
                            "line": 33,
                            "label": "plan file",
                        },
                    }
                ],
            }
            app._update_file_context_footer_hints()
            left_binding = app._bindings.key_to_bindings["ctrl+left"][0]
            right_binding = app._bindings.key_to_bindings["ctrl+right"][0]
            f9_binding = app._bindings.key_to_bindings["f9"][0]
            f10_binding = app._bindings.key_to_bindings["f10"][0]
            self.assertEqual(left_binding.description, "Prev (focus plan files)")
            self.assertEqual(right_binding.description, "Next (focus plan files)")
            self.assertEqual(f9_binding.description, "Primary (open_file claudecode_py/runtime/query_loop.py:33 plan file)")
            self.assertEqual(
                f10_binding.description,
                "Secondary (open_file claudecode_py/runtime/query_loop.py:33 plan file (fallback))",
            )
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_refresh_selected_task_detail_prefers_describe_task_detail(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_refresh_task_detail"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        describe_calls: list[str] = []
        open_calls: list[str] = []
        app.state.selected_task_id = "task-123"
        session.describe_task_detail = lambda task_id: describe_calls.append(task_id) or f"detail for {task_id}"  # type: ignore[method-assign]
        session.open_task_detail = lambda task_id: open_calls.append(task_id) or f"open detail for {task_id}"  # type: ignore[method-assign]
        session.task_execution_detail_metadata = lambda task_id: {  # type: ignore[method-assign]
            "task_surface": "background_execution",
            "execution_mode": "background",
            "execution_policy": "review",
            "execution_policy_source": "session_execution_contract",
            "allowed_tools": ["read_file"],
            "allowed_bash_prefixes": ["git diff"],
            "read_only_subagents": False,
            "workspace_mode": "snapshot",
            "workspace_health": "healthy",
        }
        session.task_workspace_detail_metadata = lambda task_id: {  # type: ignore[method-assign]
            "workspace_action": "cleanup",
            "workspace_target": "orphan-agent",
            "workspace_health_before": "orphaned",
            "workspace_health_after": "healthy",
            "workspace_planned_paths": ["C:/tmp/orphan-agent"],
            "workspace_applied_paths": ["C:/tmp/orphan-agent"],
            "workspace_failure_reason": None,
        }
        session.checklist_task_detail_metadata = lambda task_id: {  # type: ignore[method-assign]
            "checklist_task_id": task_id,
            "checklist_task_list_id": "session-1",
            "checklist_subject": "Inspect runtime",
            "checklist_description": "Inspect session.py",
            "checklist_active_form": "Inspecting runtime",
            "checklist_status": "in_progress",
            "checklist_owner": "assistant",
            "checklist_blocks": ["task-b"],
            "checklist_blocked_by": ["task-a"],
            "checklist_metadata": {"area": "runtime"},
            "checklist_created_at": "2026-05-01T00:00:00+00:00",
            "checklist_updated_at": "2026-05-02T00:00:00+00:00",
            "checklist_total_tasks": 2,
            "checklist_in_progress_tasks": 1,
        }

        try:
            app._refresh_selected_task_detail()

            self.assertEqual(describe_calls, ["task-123"])
            self.assertEqual(open_calls, [])
            self.assertEqual(app.state.task_detail_text, "detail for task-123")
            self.assertEqual(app.state.task_detail_execution_metadata["task_surface"], "background_execution")
            self.assertEqual(app.state.task_detail_workspace_metadata["workspace_target"], "orphan-agent")
            self.assertEqual(app.state.task_detail_checklist_metadata["checklist_subject"], "Inspect runtime")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_task_progress_event_refreshes_selected_task_detail(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_task_progress_refresh"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        app.state.selected_task_id = "task-123"
        seen: list[str] = []
        session.describe_task_detail = lambda task_id: seen.append(task_id) or f"detail for {task_id}"  # type: ignore[method-assign]
        session.task_execution_detail_metadata = lambda task_id: {  # type: ignore[method-assign]
            "task_surface": "background_execution",
            "execution_mode": "background",
            "execution_policy": "review",
            "execution_policy_source": "session_execution_contract",
            "allowed_tools": ["read_file"],
            "allowed_bash_prefixes": ["git diff"],
            "read_only_subagents": False,
            "workspace_mode": "snapshot",
            "workspace_health": "healthy",
        }
        session.task_workspace_detail_metadata = lambda task_id: {  # type: ignore[method-assign]
            "workspace_action": "cleanup",
            "workspace_target": "orphan-agent",
            "workspace_health_before": "orphaned",
            "workspace_health_after": "healthy",
            "workspace_planned_paths": ["C:/tmp/orphan-agent"],
            "workspace_applied_paths": ["C:/tmp/orphan-agent"],
            "workspace_failure_reason": None,
        }

        try:
            app._record_runtime_event(
                RuntimeEvent(
                    kind="task_progress",
                    message="cleanup progress 1/1 | target=orphan-agent",
                    task_id="task-123",
                )
            )

            self.assertEqual(seen, ["task-123"])
            self.assertEqual(app.state.task_detail_text, "detail for task-123")
            self.assertEqual(app.state.task_detail_execution_metadata["execution_mode"], "background")
            self.assertEqual(app.state.task_detail_workspace_metadata["workspace_target"], "orphan-agent")
            self.assertIn("[task] cleanup progress 1/1 | target=orphan-agent", app.state.events)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_task_detail_navigation_keys_jump_back_to_plan_views(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_task_nav_keys"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        app.state.selected_task_id = "task-123"

        try:
            app.state.task_detail_view = "advisor"
            app.action_execute_lineage_show()
            self.assertEqual(app.state.plan_panel_view, "advisor")
            app.action_execute_lineage_default_action()
            self.assertEqual(app.state.plan_panel_view, "execution")

            app.state.task_detail_view = "drift"
            app.action_execute_lineage_show()
            self.assertEqual(app.state.plan_panel_view, "execution")
            app.action_execute_lineage_default_action()
            self.assertEqual(app.state.plan_panel_view, "advisor")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_execute_lineage_show_runs_command_immediately(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_lineage_show"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app.state.set_plan_panel_view("lineage")
        app._selected_lineage_context = lambda: {  # type: ignore[method-assign]
            "artifact_id": "plan-123",
            "default_action": "/planning revert plan-123",
        }
        app._render = lambda: None  # type: ignore[method-assign]

        try:
            with patch("claudecode_py.tui.app._handle_repl_command", return_value=(True, "lineage detail")):
                app.action_execute_lineage_show()

            self.assertIn("[You]\n/planning show plan-123", app.state.messages)
            self.assertIn("[System]\nlineage detail", app.state.messages)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_execute_lineage_default_action_runs_command_execution(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_lineage_exec"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app.state.set_plan_panel_view("lineage")
        app._selected_lineage_context = lambda: {  # type: ignore[method-assign]
            "artifact_id": "plan-123",
            "default_action": "/planning derive map runtime",
        }
        app._render = lambda: None  # type: ignore[method-assign]
        captured: list[tuple[str, CommandExecution | None]] = []
        app._run_prompt = lambda prompt, execution=None: captured.append((prompt, execution))  # type: ignore[method-assign]

        try:
            with patch(
                "claudecode_py.tui.app._handle_repl_command",
                return_value=(
                    True,
                    CommandExecution(
                        prompt="expanded prompt",
                        progress_message="Running derived plan",
                    ),
                ),
            ):
                app.action_execute_lineage_default_action()

            self.assertIn("[You]\n/planning derive map runtime", app.state.messages)
            self.assertTrue(app.state.busy)
            self.assertIn("Running derived plan", app.state.events)
            self.assertEqual(captured[0][0], "expanded prompt")
            self.assertIsInstance(captured[0][1], CommandExecution)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_execute_lineage_default_action_syncs_focus_after_revert(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_lineage_revert"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app.state.set_plan_panel_view("lineage")
        app.state.selected_plan_lineage_index = 3
        app._selected_lineage_context = lambda: {  # type: ignore[method-assign]
            "artifact_id": "plan-older",
            "default_action": "/planning revert plan-older",
        }
        app._render = lambda: None  # type: ignore[method-assign]
        session.active_plan_lineage_index = lambda: 1  # type: ignore[method-assign]

        try:
            with patch(
                "claudecode_py.tui.app._handle_repl_command",
                return_value=(True, "Reactivated planning artifact plan-older."),
            ):
                app.action_execute_lineage_default_action()

            self.assertEqual(app.state.selected_plan_lineage_index, 1)
            self.assertIn("[You]\n/planning revert plan-older", app.state.messages)
            self.assertIn("[System]\nReactivated planning artifact plan-older.", app.state.messages)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_execute_lineage_default_action_syncs_focus_after_async_derive(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_lineage_follow"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app.state.set_plan_panel_view("lineage")
        app.state.selected_plan_lineage_index = 0
        app._selected_lineage_context = lambda: {  # type: ignore[method-assign]
            "artifact_id": "plan-active",
            "default_action": "/planning derive refine runtime",
        }
        app._render = lambda: None  # type: ignore[method-assign]
        captured: list[tuple[str, CommandExecution | None]] = []
        app._run_prompt = lambda prompt, execution=None: captured.append((prompt, execution))  # type: ignore[method-assign]
        session.active_plan_lineage_index = lambda: 2  # type: ignore[method-assign]

        try:
            with patch(
                "claudecode_py.tui.app._handle_repl_command",
                return_value=(
                    True,
                    CommandExecution(
                        prompt="expanded prompt",
                        progress_message="Running derived plan",
                    ),
                ),
            ):
                app.action_execute_lineage_default_action()

            self.assertTrue(app._follow_active_lineage_after_turn)
            app._finish_turn_output("Derived planning artifact plan-new.")
            self.assertEqual(app.state.selected_plan_lineage_index, 2)
            app._finish_prompt()
            self.assertFalse(app._follow_active_lineage_after_turn)
            self.assertEqual(captured[0][0], "expanded prompt")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_background_registry_selection_cycles_in_tui(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_background_selection"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        session.background_registry_payload = lambda: {  # type: ignore[method-assign]
            "background_registry_count": 2,
            "background_registry_selected_bg_id": "bg-123",
            "background_registry_entries": [
                {
                    "background_session_id": "bg-123",
                    "status": "running",
                    "background_continuation_category": "live attachable",
                    "background_primary_action": "pyclaude attach bg-123",
                },
                {
                    "background_session_id": "bg-456",
                    "status": "completed",
                    "background_continuation_category": "saved resumable",
                    "background_primary_action": "pyclaude --resume-session session-456 repl",
                },
            ],
        }  # type: ignore[assignment]

        try:
            app.action_select_next_background_session()
            self.assertEqual(app.state.selected_background_registry_index, 1)
            self.assertEqual(app.state.selected_background_registry_bg_id, "bg-456")
            self.assertIn("background selection: bg-456", app.state.events)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_execute_background_primary_action_resumes_saved_session(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_background_resume"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        save_transcript(
            SessionConfig(cwd=cwd, interactive=False),
            SessionState(
                session_id="saved-bg-session",
                messages=[{"role": "user", "content": [{"type": "text", "text": "resume"}]}],
            ),
        )
        record = create_background_session(
            cwd,
            prompt="saved background task",
            provider="openai-compatible",
            model="gpt-test",
            status="completed",
        )
        update_background_session(cwd, record.bg_id, session_id="saved-bg-session")
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        resumed: list[str] = []
        app._resume_background_session = lambda session_id: resumed.append(session_id)  # type: ignore[method-assign]

        try:
            app.action_execute_background_primary_action()
            self.assertEqual(resumed, ["saved-bg-session"])
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_queue_background_followup_uses_pending_input_flow(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_background_followup"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        queued: list[tuple[str, str]] = []
        session.background_registry_payload = lambda: {  # type: ignore[method-assign]
            "background_registry_count": 1,
            "background_registry_selected_bg_id": "bg-123",
            "background_registry_entries": [
                {
                    "background_session_id": "bg-123",
                    "status": "running",
                    "background_live_attachable": True,
                    "background_continuation_category": "live attachable",
                    "background_primary_action": "pyclaude attach bg-123",
                }
            ],
        }  # type: ignore[assignment]
        session.queue_background_message = (  # type: ignore[method-assign]
            lambda bg_id, prompt: queued.append((bg_id, prompt)) or f"queued {bg_id}: {prompt}"
        )

        try:
            app.action_queue_background_followup()
            self.assertIsNotNone(app.state.pending_background_followup)
            app._submit_pending_background_followup("please continue")
            self.assertEqual(queued, [("bg-123", "please continue")])
            self.assertIsNone(app.state.pending_background_followup)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_rewind_boundary_selection_cycles_in_tui(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_rewind_selection"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        session.memory_surface_payload = lambda: {  # type: ignore[method-assign]
            "memory_rewindable_boundary_count": 2,
        }  # type: ignore[assignment]
        session.rewind_boundary_preview_payload = lambda selector="1": {  # type: ignore[method-assign]
            "selector_index": int(selector),
            "boundary_id": f"hb-{selector}",
            "show_action": f"/rewind show {selector}",
            "apply_action": f"/rewind apply {selector}",
        }

        try:
            app.action_select_next_rewind_boundary()
            self.assertEqual(app.state.selected_rewind_boundary_index, 1)
            self.assertIn("rewind boundary selection: hb-2", app.state.events)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_preview_and_apply_selected_rewind_boundary_use_navigation_commands(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_tui_app_rewind_actions"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        app = PyClaudeTui(session)
        app._render = lambda: None  # type: ignore[method-assign]
        executed: list[str] = []
        session.memory_surface_payload = lambda: {  # type: ignore[method-assign]
            "memory_rewindable_boundary_count": 1,
        }  # type: ignore[assignment]
        session.rewind_boundary_preview_payload = lambda selector="1": {  # type: ignore[method-assign]
            "selector_index": int(selector),
            "boundary_id": "hb-1",
            "show_action": "/rewind show 1",
            "apply_action": "/rewind apply 1",
        }
        app._execute_navigation_command = lambda command: executed.append(command)  # type: ignore[method-assign]

        try:
            app.action_preview_selected_rewind_boundary()
            app.action_apply_selected_rewind_boundary()
            self.assertEqual(executed, ["/rewind show 1", "/rewind apply 1"])
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

