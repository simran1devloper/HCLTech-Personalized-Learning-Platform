"""Typed, validated content blocks. Rejects unsafe HTML/scripts and invalid Mermaid."""
from __future__ import annotations

import re
from typing import Annotated, ClassVar, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator

_UNSAFE = re.compile(r"<\s*/?\s*(script|iframe|object|embed|style|link|meta|form|svg|math)\b|javascript\s*:|data\s*:\s*text/html|\bon\w+\s*=",
                     re.IGNORECASE)
_ANY_TAG = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
MERMAID_TYPES = ("flowchart", "graph", "sequenceDiagram", "classDiagram", "stateDiagram", "stateDiagram-v2",
                 "erDiagram", "gantt", "pie", "mindmap", "journey")
_MERMAID_BAD = re.compile(r"(%%\{\s*init|click\s+\w+\s+(call|href)|javascript:|<\s*script|\bon\w+\s*=)", re.IGNORECASE)


def assert_safe_text(v: str) -> str:
    if _UNSAFE.search(v):
        raise ValueError("unsafe markup rejected")
    return v


def validate_mermaid(src: str) -> str:
    s = src.strip()
    if not s or len(s) > 5000:
        raise ValueError("mermaid source empty or too long")
    if _MERMAID_BAD.search(s):
        raise ValueError("mermaid directive not allowed")
    first = s.splitlines()[0].strip()
    if first.split()[0] not in MERMAID_TYPES:
        raise ValueError("unsupported or missing mermaid diagram type")
    for open_c, close_c in ("[]", "()", "{}"):
        # bracket balance (outside quoted labels)
        t = re.sub(r'"[^"\n]*"', "", s)
        if t.count(open_c) != t.count(close_c):
            raise ValueError("unbalanced brackets in mermaid source")
    if first.split()[0] in ("flowchart", "graph") and len(s.splitlines()) < 2:
        raise ValueError("flowchart has no nodes")
    return s


def _walk_strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _walk_strings(x)
    elif isinstance(v, BaseModel):
        for f in type(v).model_fields:
            yield from _walk_strings(getattr(v, f))


class _Base(BaseModel):
    #: fields rendered strictly as literal text (never as HTML) and therefore allowed to contain markup characters
    _literal_fields: ClassVar[set[str]] = set()

    @model_validator(mode="after")
    def _no_unsafe(self):
        for f in type(self).model_fields:
            if f in self._literal_fields or f == "type":
                continue
            for text in _walk_strings(getattr(self, f)):
                assert_safe_text(text)
        return self


class TextBlock(_Base):
    type: Literal["text"] = "text"
    markdown: str = Field(max_length=20000)


class CodeBlock(_Base):
    type: Literal["code"] = "code"
    language: str = Field(pattern=r"^[a-zA-Z0-9_+#-]{1,20}$")
    code: str = Field(max_length=10000)
    caption: str = ""
    _literal_fields: ClassVar[set[str]] = {"code"}  # code samples may legitimately contain markup


class TableBlock(_Base):
    type: Literal["table"] = "table"
    headers: list[str] = Field(min_length=1, max_length=12)
    rows: list[list[str]] = Field(max_length=100)

    @model_validator(mode="after")
    def _rect(self):
        if any(len(r) != len(self.headers) for r in self.rows):
            raise ValueError("table rows must match header width")
        return self


class MermaidBlock(BaseModel):
    type: Literal["mermaid"] = "mermaid"
    source: str
    caption: str = ""

    @field_validator("source")
    @classmethod
    def _v(cls, v):
        return validate_mermaid(v)


class MathBlock(_Base):
    type: Literal["math"] = "math"
    latex: str = Field(max_length=1000)


class QuizBlock(_Base):
    type: Literal["quiz"] = "quiz"
    question_ids: list[str] = Field(min_length=1)


class Flashcard(_Base):
    front: str
    back: str


class FlashcardsBlock(BaseModel):
    type: Literal["flashcards"] = "flashcards"
    cards: list[Flashcard] = Field(min_length=1, max_length=50)


class CaseStudyBlock(_Base):
    type: Literal["case_study"] = "case_study"
    title: str
    scenario: str
    questions: list[str] = []


class ExerciseBlock(_Base):
    type: Literal["exercise"] = "exercise"
    prompt: str
    hints: list[str] = []
    solution_outline: str = ""


class TutorialBlock(_Base):
    type: Literal["tutorial"] = "tutorial"
    steps: list[str] = Field(min_length=1, max_length=30)


Block = Annotated[Union[TextBlock, CodeBlock, TableBlock, MermaidBlock, MathBlock, QuizBlock, FlashcardsBlock,
                        CaseStudyBlock, ExerciseBlock, TutorialBlock], Field(discriminator="type")]


class SourceRef(BaseModel):
    title: str
    url: str
    resource_id: str | None = None


class LearningModule(BaseModel):
    skill_id: str
    title: str
    target_level: float = Field(ge=0, le=5)
    learning_objectives: list[str] = Field(min_length=1)
    prerequisites: list[str] = []
    concept_explanation: list[Block] = Field(min_length=1)
    practical_examples: list[Block] = Field(min_length=1)
    common_mistakes: list[Block] = Field(min_length=1)
    real_world_applications: list[Block] = []
    knowledge_checks: list[Block] = Field(min_length=1)
    further_resources: list[SourceRef] = []
    sources: list[SourceRef] = []  # references backing factual claims

    def all_blocks(self):
        for f in ("concept_explanation", "practical_examples", "common_mistakes", "real_world_applications", "knowledge_checks"):
            yield from getattr(self, f)


def review_module(m: LearningModule) -> list[str]:
    """Cheap deterministic review pass; the LLM reviewer is only needed for factual claims."""
    issues = []
    if not m.sources and not m.further_resources:
        issues.append("no source references: factual claims are unsupported")
    if not any(isinstance(b, CodeBlock) for b in m.practical_examples) and m.target_level >= 2:
        issues.append("practical_examples should include a code block at this level")
    for b in m.all_blocks():
        if isinstance(b, TextBlock) and len(b.markdown.strip()) < 20:
            issues.append("trivially short text block")
    return issues
