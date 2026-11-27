from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class QuestionType(str, Enum):
    MCQ = "multiple_choice"
    MULTI = "multiple_select"
    TRUE_FALSE = "true_false"
    SHORT = "short_answer"
    CODING = "coding"
    DEBUGGING = "debugging"
    SCENARIO = "scenario"
    SYSTEM_DESIGN = "system_design"
    PRACTICAL = "practical"


class RubricCriterion(BaseModel):
    id: str
    description: str
    weight: float = Field(1.0, gt=0)
    keywords_any: list[str] = []  # criterion met if any keyword group member is present
    keywords_all: list[str] = []  # ... and all of these are present


class Question(BaseModel):
    question_id: str
    skill_id: str
    type: QuestionType
    prompt: str
    difficulty: int = Field(ge=1, le=5)
    options: list[str] = []
    correct: list[int] = []  # option indices (MCQ/MULTI/TRUE_FALSE: 0=True,1=False)
    accepted_answers: list[str] = []  # SHORT
    rubric: list[RubricCriterion] = []  # SCENARIO / SYSTEM_DESIGN / PRACTICAL (and SHORT alternative)
    test_cases: list[dict[str, Any]] = []  # CODING / DEBUGGING, run in sandbox only
    objective: str = ""  # learning objective this question evidences
    explanation: str = ""  # why the right answer is right
    practical: bool = False  # True => evidences applied skill rather than recall
    provenance: str = "authored"  # authored | llm:<provider>/<model> | imported
    version: int = 1

    @model_validator(mode="after")
    def _shape(self):
        t = self.type
        if t in (QuestionType.MCQ, QuestionType.MULTI):
            if len(self.options) < 2 or not self.correct or any(not 0 <= i < len(self.options) for i in self.correct):
                raise ValueError("choice questions need >=2 options and valid correct indices")
            if t == QuestionType.MCQ and len(self.correct) != 1:
                raise ValueError("multiple_choice needs exactly one correct option")
        if t == QuestionType.TRUE_FALSE:
            self.options = ["True", "False"]
            if len(self.correct) != 1 or self.correct[0] not in (0, 1):
                raise ValueError("true_false needs correct=[0|1]")
        if t == QuestionType.SHORT and not (self.accepted_answers or self.rubric):
            raise ValueError("short_answer needs accepted_answers or a rubric")
        if t in (QuestionType.SCENARIO, QuestionType.SYSTEM_DESIGN, QuestionType.PRACTICAL) and not self.rubric:
            raise ValueError(f"{t.value} needs a rubric")
        if t in (QuestionType.CODING, QuestionType.DEBUGGING) and not self.test_cases:
            raise ValueError(f"{t.value} needs test_cases")
        if t in (QuestionType.CODING, QuestionType.DEBUGGING, QuestionType.PRACTICAL):
            self.practical = True
        return self


class GradeResult(BaseModel):
    question_id: str
    earned: float  # 0..1, partial credit allowed
    correct: bool  # earned == 1
    feedback: str
    needs_review: bool = False  # could not be graded deterministically
