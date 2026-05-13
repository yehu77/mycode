from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class QuestionOption:
    label: str
    description: str


@dataclass(slots=True, frozen=True)
class UserQuestion:
    header: str
    question: str
    options: tuple[QuestionOption, ...]
    multi_select: bool = False


@dataclass(slots=True, frozen=True)
class UserQuestionRequest:
    questions: tuple[UserQuestion, ...]


@dataclass(slots=True, frozen=True)
class UserQuestionResponse:
    answers: dict[str, str] = field(default_factory=dict)
    canceled: bool = False

