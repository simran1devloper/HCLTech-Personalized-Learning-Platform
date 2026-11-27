from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    offset: int
    limit: int


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class SkillOut(BaseModel):
    skill_id: str
    slug: str
    name: str
    description: str
    category: str
    domain: str
    difficulty: int
    aliases: list[str]
    parent_skill_id: str | None
    status: str
    prerequisites: list[str] = []


class DiscoverSkillsIn(BaseModel):
    names: list[str] = Field(min_length=1, max_length=20)


class GoalIn(BaseModel):
    role_slug: str = Field(min_length=2, max_length=80)
    hours_per_week: float = Field(5, gt=0, le=80)


class AssessmentCreateIn(BaseModel):
    skill: str = Field(min_length=1, max_length=80)
    n_questions: int = Field(5, ge=1, le=20)


class QuestionOut(BaseModel):
    """Learner-facing view: no answers, rubric, tests or explanations."""
    question_id: str
    type: str
    prompt: str
    options: list[str]
    difficulty: int
    objective: str


class AssessmentOut(BaseModel):
    assessment_id: str
    skill_id: str
    questions: list[QuestionOut]


class SubmitIn(BaseModel):
    answers: dict[str, Any] = Field(max_length=50)  # question_id -> int | list[int] | str


class RoadmapGenerateIn(BaseModel):
    role_slug: str
    hours_per_week: float = Field(5, gt=0, le=80)


class ResourceDiscoverIn(BaseModel):
    skill: str = Field(min_length=1, max_length=80)
    limit: int = Field(10, ge=1, le=25)


class TutorOptionsIn(BaseModel):
    skill: str | None = Field(None, max_length=80)
    language: Literal["en", "hinglish"] = "en"
    mode: Literal["auto", "learn", "code", "practice", "interview", "system_design", "revise"] = "auto"
    level: Literal["auto", "beginner", "intermediate", "advanced"] = "auto"
    career_goal: str | None = Field(None, max_length=80)  # a role slug; overrides the stored goal for this reply only


class TutorChatIn(TutorOptionsIn):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = Field(None, max_length=64)
    client_message_id: str | None = Field(None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")  # idempotency of one question

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be blank")
        return v


class RegenerateIn(TutorOptionsIn):
    pass


class ConversationCreateIn(BaseModel):
    title: str | None = Field(None, max_length=80)
    language: Literal["en", "hinglish"] = "en"


class ConversationPatchIn(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=80)
    pinned: bool | None = None


class FeedbackIn(BaseModel):
    rating: Literal["up", "down"] | None
    comment: str | None = Field(None, max_length=1000)
