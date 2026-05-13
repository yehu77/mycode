from __future__ import annotations

from ..interactions import QuestionOption, UserQuestion, UserQuestionRequest
from .base import BaseTool


class AskUserQuestionTool(BaseTool):
    name = "ask_user_question"
    description = "Ask the user one or more structured multiple-choice questions."
    read_only = True
    concurrency_safe = True
    deferred = True
    search_terms = ("user question", "multiple choice", "clarify preference")
    input_schema = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "header": {"type": "string"},
                        "question": {"type": "string"},
                        "multi_select": {"type": "boolean"},
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["label", "description"],
                            },
                        },
                    },
                    "required": ["header", "question", "options"],
                },
            },
        },
        "required": ["questions"],
    }

    def execute(self, tool_input: dict, ctx):
        if not 1 <= len(tool_input["questions"]) <= 4:
            raise ValueError("ask_user_question requires 1-4 questions.")
        request = UserQuestionRequest(
            questions=tuple(self._parse_question(item) for item in tool_input["questions"])
        )
        response = ctx.session.ask_user_questions(request)
        if response.canceled:
            return "User declined to answer the requested questions."
        lines = ["User answers:"]
        for question in request.questions:
            answer = response.answers.get(question.question, response.answers.get(question.header, ""))
            lines.append(f"- {question.question}: {answer}")
        return "\n".join(lines)

    def _parse_question(self, payload: dict) -> UserQuestion:
        options = tuple(
            QuestionOption(
                label=str(option["label"]),
                description=str(option["description"]),
            )
            for option in payload.get("options", [])
        )
        if not 1 <= len(options) <= 4:
            raise ValueError("ask_user_question requires 1-4 options per question.")
        return UserQuestion(
            header=str(payload["header"]),
            question=str(payload["question"]),
            options=options,
            multi_select=bool(payload.get("multi_select", False)),
        )
