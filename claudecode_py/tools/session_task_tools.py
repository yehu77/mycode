from __future__ import annotations

from typing import Any

from ..permissions import ApprovalRequest
from .base import BaseTool


def _optional_metadata_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": True,
        "description": "Arbitrary metadata. Set values to null to delete those keys on update.",
    }


class _ChecklistWriteTool(BaseTool):
    read_only = False
    concurrency_safe = False

    def approval_request(self, tool_input: dict[str, Any], ctx=None) -> ApprovalRequest:
        return ApprovalRequest(
            tool_name=self.name,
            reason=self.description,
            risk_level="read",
            approval_key=self.name,
        )


class TodoWriteTool(_ChecklistWriteTool):
    name = "todo_write"
    description = (
        "Replace the session checklist using the older todo_write compatibility format. "
        "Prefer session_task_list/get/create/update for incremental task management; use todo_write when "
        "you intentionally want to rewrite the full checklist."
    )
    search_terms = ("todo", "checklist", "task v1", "compatibility")
    input_schema = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The full replacement todo list.",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                        "active_form": {"type": "string"},
                        "activeForm": {"type": "string"},
                    },
                    "required": ["content", "status"],
                },
            },
            "task_list_id": {
                "type": "string",
                "description": "Optional task list id. Defaults to the current session id.",
            },
        },
        "required": ["todos"],
    }

    def execute(self, tool_input: dict[str, Any], ctx):
        todos = tool_input.get("todos")
        if not isinstance(todos, list):
            raise ValueError("todos must be an array.")
        return ctx.session.todo_write(
            todos,
            task_list_id=(
                str(tool_input["task_list_id"])
                if tool_input.get("task_list_id") is not None
                else None
            ),
        )


class SessionTaskCreateTool(_ChecklistWriteTool):
    name = "session_task_create"
    description = (
        "Create a checklist task in the current session-scoped task list. "
        "Call session_task_list first to avoid duplicates, and prefer updating an existing task when the "
        "same outcome is already tracked. When an obvious duplicate already exists, this tool can return "
        "created=false with duplicate_guard guidance instead of making a new task."
    )
    search_terms = ("task v2", "checklist", "create task", "session task")
    input_schema = {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "A short task title."},
            "description": {"type": "string", "description": "What needs to be done."},
            "active_form": {
                "type": "string",
                "description": 'Present continuous form shown while in progress, such as "Running tests".',
            },
            "activeForm": {
                "type": "string",
                "description": "Alias for active_form.",
            },
            "metadata": _optional_metadata_schema(),
            "task_list_id": {
                "type": "string",
                "description": "Optional task list id. Defaults to the current session id.",
            },
        },
        "required": ["subject", "description"],
    }

    def execute(self, tool_input: dict[str, Any], ctx):
        subject = str(tool_input.get("subject", "")).strip()
        description = str(tool_input.get("description", "")).strip()
        if not subject:
            raise ValueError("subject is required.")
        if not description:
            raise ValueError("description is required.")
        active_form = str(tool_input.get("active_form") or tool_input.get("activeForm") or "").strip()
        if not active_form:
            active_form = description
        return ctx.session.create_checklist_task(
            subject=subject,
            description=description,
            active_form=active_form,
            metadata=dict(tool_input.get("metadata") or {}),
            task_list_id=(
                str(tool_input["task_list_id"])
                if tool_input.get("task_list_id") is not None
                else None
            ),
        )


class SessionTaskListTool(BaseTool):
    name = "session_task_list"
    description = (
        "List checklist tasks in the current session-scoped task list. "
        "Use this before creating new checklist tasks, after completing work, and to find the next task to advance."
    )
    read_only = True
    concurrency_safe = True
    search_terms = ("task v2", "checklist", "list tasks", "session task")
    input_schema = {
        "type": "object",
        "properties": {
            "task_list_id": {
                "type": "string",
                "description": "Optional task list id. Defaults to the current session id.",
            }
        },
    }

    def execute(self, tool_input: dict[str, Any], ctx):
        tasks = ctx.session.list_checklist_tasks(
            task_list_id=(
                str(tool_input["task_list_id"])
                if tool_input.get("task_list_id") is not None
                else None
            )
        )
        return {"task_list_id": ctx.session.checklist_task_list_id(), "tasks": tasks}


class SessionTaskGetTool(BaseTool):
    name = "session_task_get"
    description = (
        "Inspect one checklist task in the current session-scoped task list. "
        "Use this to read the latest task state before updating it."
    )
    read_only = True
    concurrency_safe = True
    search_terms = ("task v2", "checklist", "task detail", "session task")
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Checklist task id."},
            "taskId": {"type": "string", "description": "Alias for task_id."},
            "task_list_id": {
                "type": "string",
                "description": "Optional task list id. Defaults to the current session id.",
            },
        },
    }

    def execute(self, tool_input: dict[str, Any], ctx):
        task_id = str(tool_input.get("task_id") or tool_input.get("taskId") or "").strip()
        if not task_id:
            raise ValueError("task_id is required.")
        task = ctx.session.get_checklist_task(
            task_id,
            task_list_id=(
                str(tool_input["task_list_id"])
                if tool_input.get("task_list_id") is not None
                else None
            ),
        )
        if task is None:
            raise ValueError("Unknown checklist task id.")
        return {"task": task}


class SessionTaskUpdateTool(_ChecklistWriteTool):
    name = "session_task_update"
    description = (
        "Update a checklist task in the current session-scoped task list. "
        "Prefer reading the latest state with session_task_get first unless the task was just created or listed "
        "in the current turn, and prefer updating tracked tasks over creating duplicates."
    )
    search_terms = ("task v2", "checklist", "update task", "session task")
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Checklist task id."},
            "taskId": {"type": "string", "description": "Alias for task_id."},
            "subject": {"type": "string"},
            "description": {"type": "string"},
            "active_form": {"type": "string"},
            "activeForm": {"type": "string", "description": "Alias for active_form."},
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed", "deleted"],
            },
            "owner": {"type": "string"},
            "add_blocks": {"type": "array", "items": {"type": "string"}},
            "addBlocks": {"type": "array", "items": {"type": "string"}, "description": "Alias for add_blocks."},
            "add_blocked_by": {"type": "array", "items": {"type": "string"}},
            "addBlockedBy": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Alias for add_blocked_by.",
            },
            "remove_blocks": {"type": "array", "items": {"type": "string"}},
            "remove_blocked_by": {"type": "array", "items": {"type": "string"}},
            "metadata": _optional_metadata_schema(),
            "task_list_id": {
                "type": "string",
                "description": "Optional task list id. Defaults to the current session id.",
            },
        },
    }

    def execute(self, tool_input: dict[str, Any], ctx):
        task_id = str(tool_input.get("task_id") or tool_input.get("taskId") or "").strip()
        if not task_id:
            raise ValueError("task_id is required.")
        return ctx.session.update_checklist_task(
            task_id,
            subject=tool_input.get("subject"),
            description=tool_input.get("description"),
            active_form=tool_input.get("active_form") or tool_input.get("activeForm"),
            status=tool_input.get("status"),
            owner=tool_input.get("owner"),
            add_blocks=list(tool_input.get("add_blocks") or tool_input.get("addBlocks") or []),
            add_blocked_by=list(tool_input.get("add_blocked_by") or tool_input.get("addBlockedBy") or []),
            remove_blocks=list(tool_input.get("remove_blocks") or []),
            remove_blocked_by=list(tool_input.get("remove_blocked_by") or []),
            metadata=dict(tool_input.get("metadata") or {}) if tool_input.get("metadata") is not None else None,
            task_list_id=(
                str(tool_input["task_list_id"])
                if tool_input.get("task_list_id") is not None
                else None
            ),
        )
