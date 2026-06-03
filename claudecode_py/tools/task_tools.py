from __future__ import annotations

from .base import BaseTool


def _background_reverse_hint(task) -> tuple[str | None, str | None]:
    metadata = task.metadata or {}
    background_session_id = str(metadata.get("background_session_id") or "").strip() or None
    explicit_reverse_hint = str(metadata.get("background_reverse_hint") or "").strip() or None
    parent_session_id = str(metadata.get("parent_session_id") or "").strip() or None
    task_role = str(metadata.get("task_role") or "").strip()
    plan_execution_mode = str(metadata.get("plan_execution_mode") or "").strip()
    child_execution_mode = str(metadata.get("child_execution_mode") or "").strip()
    is_background_linked = (
        task_role in {"background", "execution"}
        or plan_execution_mode == "background_agent"
        or child_execution_mode == "background-agent"
    )
    if not is_background_linked:
        return None, None
    if background_session_id:
        return (
            background_session_id,
            explicit_reverse_hint
            or f"pyclaude ps {background_session_id} | pyclaude logs {background_session_id} summary",
        )
    reverse_hint = "/tasks active | /status workflow"
    if parent_session_id:
        reverse_hint = f"owning_session={parent_session_id}; actions={reverse_hint}"
    return parent_session_id, reverse_hint


class TaskListTool(BaseTool):
    name = "task_list"
    description = "List background tasks created in the current session."
    read_only = True
    concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {},
    }

    def execute(self, tool_input: dict, ctx):
        tasks = ctx.task_manager.list()
        if not tasks:
            return "No tasks."
        lines = []
        for task in tasks:
            updated = task.updated_at or task.created_at
            progress = f"  progress={task.progress_summary}" if task.progress_summary else ""
            owning_session_id, reverse_hint = _background_reverse_hint(task)
            reverse_bits = []
            if owning_session_id:
                reverse_bits.append(f"background_session_id={owning_session_id}")
            if reverse_hint:
                reverse_bits.append(f"background_reverse_hint={reverse_hint}")
            reverse_suffix = f"  {' '.join(reverse_bits)}" if reverse_bits else ""
            lines.append(
                f"{task.id}  status={task.status}  kind={task.kind}  updated={updated}  "
                f"description={task.description}{progress}{reverse_suffix}"
            )
        return "\n".join(lines)


class TaskGetTool(BaseTool):
    name = "task_get"
    description = "Inspect a background task by id."
    read_only = True
    concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The task id returned by the agent tool."},
            "tail_lines": {
                "type": "integer",
                "description": "Optional number of trailing output lines to include instead of the full output.",
            },
        },
        "required": ["task_id"],
    }

    def execute(self, tool_input: dict, ctx):
        task = ctx.task_manager.get(tool_input["task_id"])
        if task is None:
            raise ValueError("Unknown task id.")
        parts = [
            f"id: {task.id}",
            f"status: {task.status}",
            f"kind: {task.kind}",
            f"description: {task.description}",
            f"created_at: {task.created_at}",
        ]
        if task.updated_at:
            parts.append(f"updated_at: {task.updated_at}")
        if task.ended_at:
            parts.append(f"ended_at: {task.ended_at}")
        if task.progress_summary:
            parts.append(f"progress_summary: {task.progress_summary}")
        if task.metadata:
            metadata_lines = [f"{key}: {value}" for key, value in sorted(task.metadata.items())]
            parts.append("metadata:\n" + "\n".join(metadata_lines))
        owning_session_id, reverse_hint = _background_reverse_hint(task)
        if owning_session_id:
            parts.append(f"background_session_id: {owning_session_id}")
        if reverse_hint:
            parts.append(f"background_reverse_hint: {reverse_hint}")
        if task.error:
            parts.append(f"error:\n{task.error}")
        if task.output:
            output = task.output
            tail_lines = tool_input.get("tail_lines")
            if tail_lines is not None:
                count = max(1, int(tail_lines))
                output_lines = output.splitlines()
                output = "\n".join(output_lines[-count:])
            parts.append(f"output:\n{output}")
        return "\n\n".join(parts)


class TaskStopTool(BaseTool):
    name = "task_stop"
    description = "Stop a background task by id."
    read_only = False
    concurrency_safe = False
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task id to stop.",
            },
        },
        "required": ["task_id"],
    }

    def execute(self, tool_input: dict, ctx):
        task = ctx.task_manager.get(tool_input["task_id"])
        if task is None:
            raise ValueError("Unknown task id.")
        stopped = ctx.task_manager.stop(tool_input["task_id"])
        return f"Stopped task {stopped.id} (status={stopped.status})"


class TaskWaitTool(BaseTool):
    name = "task_wait"
    description = "Wait for a background task to reach a terminal state."
    read_only = True
    concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task id to wait for.",
            },
            "timeout_sec": {
                "type": "number",
                "description": "Optional timeout in seconds.",
            },
        },
        "required": ["task_id"],
    }

    def execute(self, tool_input: dict, ctx):
        task_id = tool_input["task_id"]
        timeout_sec = tool_input.get("timeout_sec")
        task = ctx.task_manager.get(task_id)
        if task is None:
            raise ValueError("Unknown task id.")
        result = ctx.task_manager.wait_for_task(
            task_id,
            None if timeout_sec is None else float(timeout_sec),
        )
        parts = [
            f"id: {result.id}",
            f"status: {result.status}",
            f"kind: {result.kind}",
            f"description: {result.description}",
        ]
        if result.progress_summary:
            parts.append(f"progress_summary: {result.progress_summary}")
        if result.error:
            parts.append(f"error:\n{result.error}")
        if result.output:
            output_lines = result.output.splitlines()
            parts.append("output_tail:\n" + "\n".join(output_lines[-20:]))
        return "\n\n".join(parts)
