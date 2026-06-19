from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..agents import BUILTIN_EXPLORE_AGENT_NAME, BUILTIN_PLAN_AGENT_NAME
from ..config import SessionConfig
from ..prompts import PromptAttachment

FULL_REMINDER_EVERY_N_ATTACHMENTS = 5
PlanWorkflowMode = Literal["five_phase", "interview"]


@dataclass(slots=True, frozen=True)
class PlanAgentInvocationBoundary:
    workflow_mode: PlanWorkflowMode
    allowed_agent_names: tuple[str, ...]
    explore_scope: str
    plan_scope: str
    main_thread_scope: str
    boundary_summary: str
    delegation_default: str


@dataclass(slots=True, frozen=True)
class PlanWorkflowBranchProfile:
    workflow_mode: PlanWorkflowMode
    branch_identity: str
    branch_summary: str
    planning_cadence: str
    first_turn_contract: str
    first_turn_summary: str
    first_turn_scan_scope: str
    first_turn_plan_expectation: str
    first_turn_question_timing: str
    first_turn_regression_guard: str
    plan_update_contract: str
    plan_update_summary: str
    plan_update_trigger: str
    plan_update_capture_scope: str
    plan_update_deferral_guard: str
    question_loop_contract: str
    turn_exit_contract: str
    turn_exit_summary: str
    clarification_channel: str
    approval_channel: str
    turn_exit_forbidden_patterns: str
    planning_agent_usage_summary: str
    explore_agent_usage_rule: str
    plan_agent_delegation_rule: str
    main_thread_design_owner: str
    followup_continuity_contract: str
    followup_continuity_summary: str
    branch_preservation_rule: str


def get_plan_mode_v2_agent_count(config: SessionConfig | None = None) -> int:
    if config is None:
        return 1
    return 1


def get_plan_mode_v2_explore_agent_count(config: SessionConfig | None = None) -> int:
    if config is None:
        return 3
    return 3


def is_plan_mode_interview_phase_enabled(config: SessionConfig | None = None) -> bool:
    if config is None:
        return False
    return bool(getattr(config, "plan_mode_interview_phase", False))


def get_plan_phase4_section() -> str:
    return (
        "### Phase 4: Final Plan\n"
        "Goal: Write your final plan to the plan file (the only file you can edit).\n"
        "- Begin with a **Context** section: explain why this change is being made, the problem or need it addresses, and the intended outcome.\n"
        "- Include only your recommended approach, not all alternatives.\n"
        "- Ensure that the plan file is concise enough to scan quickly, but detailed enough to execute effectively.\n"
        "- Include the paths of critical files to be modified.\n"
        "- Reference existing functions and utilities you found that should be reused, with their file paths.\n"
        "- Include a verification section describing how to test the changes end-to-end."
    )


def plan_workflow_mode(config: SessionConfig | None = None) -> PlanWorkflowMode:
    return (
        "interview"
        if is_plan_mode_interview_phase_enabled(config)
        else "five_phase"
    )


def build_plan_workflow_branch_profile(
    workflow_mode: PlanWorkflowMode,
) -> PlanWorkflowBranchProfile:
    if workflow_mode == "interview":
        return PlanWorkflowBranchProfile(
            workflow_mode="interview",
            branch_identity="interview_branch",
            branch_summary=(
                "Iterative interview planning branch: quick scan, draft a skeleton plan, "
                "ask the user early, and refine the plan file incrementally."
            ),
            planning_cadence="iterative_interview_loop",
            first_turn_contract="quick_scan_then_skeleton_plan_then_first_question",
            first_turn_summary=(
                "Quickly scan a few key files, write a skeleton plan (headers and rough notes), "
                "then ask the first round of user questions."
            ),
            first_turn_scan_scope="quickly_scan_a_few_key_files_only",
            first_turn_plan_expectation=(
                "write_a_skeleton_plan_with_headers_and_rough_notes_before_questioning"
            ),
            first_turn_question_timing="ask_first_round_of_questions_after_skeleton_plan",
            first_turn_regression_guard=(
                "do_not_explore_exhaustively_or_finish_the_final_plan_before_first_questions"
            ),
            plan_update_contract="incremental_plan_updates_during_discovery",
            plan_update_summary=(
                "After each meaningful discovery, immediately update the plan file instead of waiting for a final write-up."
            ),
            plan_update_trigger="update_plan_file_after_each_meaningful_discovery",
            plan_update_capture_scope=(
                "capture_relevant_findings_reuse_points_decisions_and_open_questions_immediately"
            ),
            plan_update_deferral_guard="do_not_defer_plan_writing_until_the_end",
            question_loop_contract="ask_user_when_code_cannot_resolve_decisions",
            turn_exit_contract="ask_user_question_or_ExitPlanMode_only",
            turn_exit_summary=(
                "Interview turns may end only by asking the user a clarification question or by calling ExitPlanMode for approval."
            ),
            clarification_channel="ask_user_question_only",
            approval_channel="ExitPlanMode_only",
            turn_exit_forbidden_patterns=(
                "no_plain_text_approval_requests_no_plain_text_stop_no_fake_approval_via_ask_user_question"
            ),
            planning_agent_usage_summary=(
                "Explore is optional and scoped in interview mode; implementation-design synthesis stays in the main thread."
            ),
            explore_agent_usage_rule="Explore_optional_for_scoped_reconnaissance_only",
            plan_agent_delegation_rule="do_not_default_to_Plan_agent_in_interview_mode",
            main_thread_design_owner="main_thread_owns_implementation_design_synthesis",
            followup_continuity_contract=(
                "sparse_reentry_reject_retry_preserve_interview_family"
            ),
            followup_continuity_summary=(
                "Later turns remain in interview-family semantics across sparse follow-up, rejected exit, reentry, and failed-turn retry."
            ),
            branch_preservation_rule=(
                "preserve_interview_family_across_followup_rejection_and_retry"
            ),
        )
    return PlanWorkflowBranchProfile(
        workflow_mode="five_phase",
        branch_identity="five_phase_branch",
        branch_summary=(
            "Staged planning branch: initial understanding, design, review, final plan, "
            "then ExitPlanMode approval."
        ),
        planning_cadence="phase_staged_workflow",
        first_turn_contract="phase1_initial_understanding_before_design",
        first_turn_summary=(
            "Use the opening turn to build initial understanding and reconnaissance before "
            "moving into explicit design."
        ),
        first_turn_scan_scope="phase1_reconnaissance_and_initial_understanding",
        first_turn_plan_expectation="do_not_jump_directly_to_final_plan_before_design",
        first_turn_question_timing="clarify_during_or_after_initial_understanding_as_needed",
        first_turn_regression_guard="do_not_skip_phase1_or_collapse_directly_into_phase4",
        plan_update_contract="final_plan_converges_in_phase4",
        plan_update_summary=(
            "Use planning phases to converge toward the final plan, with the decisive plan write concentrated in Phase 4."
        ),
        plan_update_trigger="phase_driven_plan_convergence",
        plan_update_capture_scope="capture_phase_findings_and_finalize_in_phase4",
        plan_update_deferral_guard="do_not_skip_phase4_final_plan_synthesis",
        question_loop_contract="clarify_after_review_or_when_needed",
        turn_exit_contract="ask_user_question_or_ExitPlanMode_only",
        turn_exit_summary=(
            "Five-phase turns may end only by asking the user a clarification question or by calling ExitPlanMode for approval."
        ),
        clarification_channel="ask_user_question_only",
        approval_channel="ExitPlanMode_only",
        turn_exit_forbidden_patterns=(
            "no_plain_text_approval_requests_no_plain_text_stop_no_fake_approval_via_ask_user_question"
        ),
        planning_agent_usage_summary=(
            "Explore handles Phase 1 reconnaissance; Plan handles Phase 2 design; review and final plan stay in the main thread."
        ),
        explore_agent_usage_rule="Explore_primary_for_phase1_reconnaissance",
        plan_agent_delegation_rule="Plan_default_design_delegate_for_phase2",
        main_thread_design_owner="main_thread_owns_review_and_final_plan_synthesis",
        followup_continuity_contract=(
            "sparse_reentry_reject_retry_preserve_five_phase_family"
        ),
        followup_continuity_summary=(
            "Later turns remain in five-phase semantics across sparse follow-up, rejected exit, reentry, and failed-turn retry."
        ),
        branch_preservation_rule=(
            "preserve_five_phase_family_across_followup_rejection_and_retry"
        ),
    )


def build_plan_agent_invocation_boundary(
    workflow_mode: PlanWorkflowMode,
) -> PlanAgentInvocationBoundary:
    if workflow_mode == "interview":
        return PlanAgentInvocationBoundary(
            workflow_mode="interview",
            allowed_agent_names=(BUILTIN_EXPLORE_AGENT_NAME,),
            explore_scope=(
                "Optional scoped codebase search and reuse reconnaissance for complex or parallelizable lookups."
            ),
            plan_scope=(
                "Keep implementation-design synthesis in the main thread; do not default to Plan-agent delegation in interview mode."
            ),
            main_thread_scope=(
                "Own iterative synthesis, plan-file updates, user questions, and final approval in the main thread."
            ),
            boundary_summary=(
                "interview boundary: Explore optional for scoped search; implementation design stays in main thread"
            ),
            delegation_default="main_thread_first",
        )
    return PlanAgentInvocationBoundary(
        workflow_mode="five_phase",
        allowed_agent_names=(BUILTIN_EXPLORE_AGENT_NAME, BUILTIN_PLAN_AGENT_NAME),
        explore_scope=(
            "Phase 1 reconnaissance: search code paths, find existing implementations, map reuse points, and reduce uncertainty."
        ),
        plan_scope=(
            "Phase 2 design: synthesize exploration findings into ordered implementation steps, critical files, reuse notes, and verification ideas."
        ),
        main_thread_scope=(
            "Keep review, user clarification, plan-file writing, and final ExitPlanMode approval in the main thread."
        ),
        boundary_summary=(
            "five_phase boundary: Explore for reconnaissance; Plan for design; review and final plan stay in main thread"
        ),
        delegation_default="phase_split",
    )


def build_plan_mode_full_attachment(
    *,
    workflow_mode: PlanWorkflowMode,
    plan_file_path: Path,
    plan_exists: bool,
    config: SessionConfig | None = None,
) -> PromptAttachment:
    if workflow_mode == "interview":
        return PromptAttachment(
            kind="plan_mode",
            text=_build_interview_workflow_text(
                plan_file_path=plan_file_path,
                plan_exists=plan_exists,
            ),
            cache_scope="dynamic",
            summary="plan_mode:interview:full",
            one_shot=False,
        )
    return PromptAttachment(
        kind="plan_mode",
        text=_build_five_phase_workflow_text(
            plan_file_path=plan_file_path,
            plan_exists=plan_exists,
            config=config,
        ),
        cache_scope="dynamic",
        summary="plan_mode:five_phase:full",
        one_shot=False,
    )


def build_plan_mode_sparse_attachment(
    *,
    workflow_mode: PlanWorkflowMode,
    plan_file_path: Path,
) -> PromptAttachment:
    branch_profile = build_plan_workflow_branch_profile(workflow_mode)
    boundary = build_plan_agent_invocation_boundary(workflow_mode)
    workflow_description = (
        "Follow iterative workflow: explore codebase, interview user, write to plan incrementally."
        if workflow_mode == "interview"
        else "Follow 5-phase workflow."
    )
    text = (
        "Plan mode still active (see full instructions earlier in conversation). "
        f"Read-only except the current session plan file ({plan_file_path}). "
        f"{workflow_description} {boundary.boundary_summary}. "
        f"{branch_profile.plan_update_summary} "
        f"{branch_profile.turn_exit_summary} "
        "Never ask about plan approval via text or ask_user_question."
    )
    return PromptAttachment(
        kind="plan_mode",
        text=text,
        cache_scope="dynamic",
        summary=f"plan_mode:{workflow_mode}:sparse",
        one_shot=False,
    )


def build_plan_mode_reentry_attachment(
    *,
    workflow_mode: PlanWorkflowMode,
    plan_file_path: Path,
) -> PromptAttachment:
    text = (
        "## Re-entering Plan Mode\n\n"
        "You are returning to plan mode after having previously exited it. "
        f"A plan file already exists at {plan_file_path} from the previous planning session.\n\n"
        "**Before proceeding with any new planning, you should:**\n"
        "1. Read the existing plan file to understand what was previously planned.\n"
        "2. Evaluate the user's current request against that plan.\n"
        "3. Decide how to proceed:\n"
        "   - **Different task**: if the user's request is for a different task, even if it is similar or related, start fresh by overwriting the existing plan.\n"
        "   - **Same task, continuing**: if this is explicitly a continuation or refinement of the exact same task, modify the existing plan while cleaning up outdated or irrelevant sections.\n"
        "4. Continue the plan process and, most importantly, always edit the plan file one way or the other before calling ExitPlanMode.\n\n"
        "Treat this as a fresh planning session. Do not assume the existing plan is relevant without evaluating it first. "
        "Stay in read-only exploration mode except for edits to the current session plan file."
    )
    return PromptAttachment(
        kind="plan_mode_reentry",
        text=text,
        cache_scope="dynamic",
        summary=f"plan_mode_reentry:{workflow_mode}",
        one_shot=True,
    )


def build_plan_mode_exit_attachment(
    *,
    workflow_mode: PlanWorkflowMode,
    plan_file_path: Path,
    approved_plan: str,
    restored_mode: str,
) -> PromptAttachment:
    approved_plan_text = approved_plan.strip() or "(plan file empty)"
    text = (
        "## Exited Plan Mode\n\n"
        "The user approved the plan and plan mode has ended. "
        f"Runtime mode is now restored to `{restored_mode}`. "
        f"The approved session plan file is located at {plan_file_path}.\n\n"
        "You can now begin implementation against the approved plan below. "
        "This is implementation-side guidance, not a request to continue planning.\n\n"
        "Approved plan:\n"
        f"{approved_plan_text}"
    )
    return PromptAttachment(
        kind="plan_mode_exit",
        text=text,
        cache_scope="dynamic",
        summary=f"plan_mode_exit:{workflow_mode}",
        one_shot=True,
    )


def _build_plan_file_info(*, plan_file_path: Path, plan_exists: bool) -> str:
    if plan_exists:
        return (
            f"A plan file already exists at {plan_file_path}. "
            "You can read it and make incremental edits using write_file, edit_file, or apply_patch."
        )
    return (
        f"No plan file exists yet. You should create your plan at {plan_file_path} "
        "using write_file, edit_file, or apply_patch."
    )


def _build_five_phase_workflow_text(
    *,
    plan_file_path: Path,
    plan_exists: bool,
    config: SessionConfig | None = None,
) -> str:
    explore_agent_name = BUILTIN_EXPLORE_AGENT_NAME
    plan_agent_name = BUILTIN_PLAN_AGENT_NAME
    agent_count = get_plan_mode_v2_agent_count(config)
    explore_agent_count = get_plan_mode_v2_explore_agent_count(config)
    plan_file_info = _build_plan_file_info(
        plan_file_path=plan_file_path,
        plan_exists=plan_exists,
    )
    multiple_plan_agents = ""
    if agent_count > 1:
        multiple_plan_agents = (
            f"- **Multiple agents**: Use up to {agent_count} Plan agents for complex tasks that benefit from different perspectives.\n"
            "\n"
            "Examples of when to use multiple agents:\n"
            "- The task touches multiple parts of the codebase.\n"
            "- It is a large refactor or architectural change.\n"
            "- There are many edge cases to consider.\n"
            "- You would benefit from exploring different approaches.\n"
            "\n"
            "Example perspectives by task type:\n"
            "- New feature: simplicity vs performance vs maintainability.\n"
            "- Bug fix: root cause vs workaround vs prevention.\n"
            "- Refactoring: minimal change vs clean architecture.\n"
        )
    return (
        "Plan mode is active. The user indicated that they do not want you to execute yet. "
        "You MUST NOT make edits outside the session plan file, run non-read-only tools, "
        "or otherwise make changes to the system.\n\n"
        "## Plan File Info\n"
        f"{plan_file_info}\n"
        "You should build your plan incrementally by writing to or editing this file. "
        "This is the only file you are allowed to edit.\n\n"
        "## Plan Workflow\n\n"
        "### Phase 1: Initial Understanding\n"
        "Goal: Gain a comprehensive understanding of the user's request by reading through code and asking them questions. "
        f"Critical: in this phase you should only use the {explore_agent_name} agent type.\n"
        "1. Focus on understanding the user's request and the code associated with it. "
        "Actively search for existing functions, utilities, and patterns that can be reused. "
        "Avoid proposing new code when suitable implementations already exist.\n"
        f"2. Launch up to {explore_agent_count} {explore_agent_name} agents IN PARALLEL (single message, multiple tool calls) to efficiently explore the codebase.\n"
        "- Use 1 agent when the task is isolated to known files, the user provided specific file paths, or you are making a small targeted change.\n"
        "- Use multiple agents when the scope is uncertain, multiple areas of the codebase are involved, or you need to understand existing patterns before planning.\n"
        f"- Quality over quantity: {explore_agent_count} agents maximum, but you should use the minimum number of agents necessary (usually just 1).\n"
        "- If using multiple agents, provide each agent with a specific search focus or area to explore. "
        "Example: one agent searches for existing implementations, another explores related components, and a third investigates testing patterns.\n\n"
        f"When prompting {explore_agent_name}, ask for reconnaissance outputs such as relevant files, existing implementations, reuse candidates, and code path traces. "
        f"{explore_agent_name} should reduce uncertainty for Phase 2, not replace the final implementation-design work.\n\n"
        "### Phase 2: Design\n"
        "Goal: Design an implementation approach.\n"
        f"Launch {plan_agent_name} agent(s) to design the implementation based on the user's intent and your Phase 1 exploration results.\n"
        f"You can launch up to {agent_count} {plan_agent_name} agent(s) in parallel.\n"
        f"When prompting {plan_agent_name}, pass in the relevant exploration findings and ask for ordered implementation steps, critical files to change, reuse notes, verification ideas, and any major tradeoffs.\n"
        f"{plan_agent_name} is the Phase 2 design role. It should synthesize reconnaissance into an execution approach, not behave like another search-only agent.\n"
        "Keep final review, user clarification, and plan-file writing in the main thread rather than delegating them to planning agents.\n"
        "\n"
        "**Guidelines:**\n"
        f"- **Default**: launch at least 1 {plan_agent_name} agent for most tasks. It helps validate your understanding and consider alternatives.\n"
        "- **Skip agents**: only for truly trivial tasks such as typo fixes, single-line changes, or simple renames.\n"
        f"{multiple_plan_agents}"
        "\n"
        "In the agent prompt:\n"
        "- Provide comprehensive background context from Phase 1 exploration, including filenames and code path traces.\n"
        "- Describe requirements and constraints.\n"
        "- Request a detailed implementation plan.\n\n"
        "### Phase 3: Review\n"
        "Goal: Review the plan(s) from Phase 2 and ensure alignment with the user's intentions.\n"
        "1. Read the critical files identified by agents to deepen your understanding.\n"
        "2. Ensure the plans align with the user's original request.\n"
        "3. Use ask_user_question to clarify any remaining questions with the user.\n\n"
        f"{get_plan_phase4_section()}\n\n"
        "### Phase 5: Call ExitPlanMode\n"
        "At the very end of your turn, once you have asked the user questions and are happy with your final plan file, you should always call ExitPlanMode to indicate that you are done planning.\n"
        "This is critical: your turn should only end with either using ask_user_question or calling ExitPlanMode. Do not stop unless it is for one of these two reasons.\n"
        "\n"
        "**Important:** Use ask_user_question only to clarify requirements or choose between approaches. Use ExitPlanMode to request plan approval. "
        "Do not ask about plan approval in any other way: no plain text approval requests and no ask_user_question for approval.\n"
        "\n"
        "Note: at any point in this workflow you should feel free to ask the user questions or clarifications using ask_user_question. "
        "Do not make large assumptions about user intent. The goal is to present a well-researched plan to the user and tie up loose ends before implementation begins."
    )


def _build_interview_workflow_text(
    *,
    plan_file_path: Path,
    plan_exists: bool,
) -> str:
    branch_profile = build_plan_workflow_branch_profile("interview")
    plan_file_info = _build_plan_file_info(
        plan_file_path=plan_file_path,
        plan_exists=plan_exists,
    )
    return (
        "Plan mode is active. The user indicated that they do not want you to execute yet. "
        "You MUST NOT make edits outside the session plan file, run non-read-only tools, "
        "or otherwise make changes to the system.\n\n"
        "## Plan File Info\n"
        f"{plan_file_info}\n"
        "The plan file above is the ONLY file you may edit. It should start as a rough skeleton and gradually become the final plan.\n\n"
        "## Iterative Planning Workflow\n"
        "You are pair-planning with the user. Explore the code to build context, ask the user questions when you hit decisions you cannot make alone, and write your findings into the plan file as you go.\n\n"
        "### The Loop\n"
        "Repeat this cycle until the plan is complete:\n"
        "1. **Explore**: Use read-only tools to read code. Look for existing functions, utilities, and patterns to reuse. "
        f"You can use the {BUILTIN_EXPLORE_AGENT_NAME} agent type to parallelize complex searches without filling your context, though for straightforward queries direct tools are simpler.\n"
        "2. **Update the plan file**: After each discovery, immediately capture what you learned. Do not wait until the end.\n"
        "3. **Ask the user**: When you hit an ambiguity or decision you cannot resolve from code alone, use ask_user_question. Then go back to step 1.\n\n"
        f"Keep implementation-design synthesis in the main thread. Do not default to {BUILTIN_PLAN_AGENT_NAME} delegation in interview mode; only use {BUILTIN_EXPLORE_AGENT_NAME} for scoped reconnaissance when it materially helps search.\n\n"
        "### First Turn\n"
        f"{branch_profile.first_turn_summary} "
        "On the first turn, limit yourself to a few key files that establish the likely scope, entry points, and reuse paths. "
        "The initial plan should stay at skeleton depth: headers and rough notes only, not a finished final plan. "
        "Do not explore exhaustively before engaging the user, and do not try to finish the final plan before asking your first round of questions.\n\n"
        "### Asking Good Questions\n"
        "- Never ask what you could find out by reading the code.\n"
        "- Batch related questions together. Prefer multi-question ask_user_question calls.\n"
        "- Focus on things only the user can answer: requirements, preferences, tradeoffs, and edge-case priorities.\n"
        "- Scale depth to the task: a vague feature request may need many rounds, while a focused bug fix may need one round or none.\n\n"
        "### Plan File Structure\n"
        "Your plan file should be divided into clear sections using markdown headers based on the request. Fill out these sections as you go.\n"
        "- Begin with a **Context** section: explain why this change is being made, the problem or need it addresses, what prompted it, and the intended outcome.\n"
        "- Include only your recommended approach, not all alternatives.\n"
        "- Ensure that the plan file is concise enough to scan quickly, but detailed enough to execute effectively.\n"
        "- Include the paths of critical files to be modified.\n"
        "- Reference existing functions and utilities you found that should be reused, with their file paths.\n"
        "- Include a verification section describing how to test the changes end-to-end.\n\n"
        "### When to Converge\n"
        "Your plan is ready when you have addressed all ambiguities and it covers: what to change, which files to modify, what existing code to reuse (with file paths), and how to verify the changes. "
        "Call ExitPlanMode when the plan is ready for approval.\n\n"
        "### Ending Your Turn\n"
        "Your turn should only end by either:\n"
        "- Using ask_user_question to gather more information.\n"
        "- Calling ExitPlanMode when the plan is ready for approval.\n"
        "\n"
        "**Important:** Use ExitPlanMode to request plan approval. Do not ask about plan approval via plain text or ask_user_question."
    )
