"""
schemas/learning_schema.py — Pydantic models for learning endpoints
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict, Any


# ─── Quiz ───

class QuizGenerateRequest(BaseModel):
    topic: str = Field(..., min_length=2, max_length=120, description="Topic to generate quiz for")


class QuizQuestion(BaseModel):
    question: str
    options: List[str] = Field(..., min_length=4)  # At least 4 options required
    correct_answer: str


class QuizGenerateResponse(BaseModel):
    topic: str
    questions: List[QuizQuestion]
    # Signed server-side copy of the quiz. Submit grades against this, not against client-supplied answers.
    quiz_token: Optional[str] = None


class QuizSubmitRequest(BaseModel):
    topic: str = Field(..., min_length=2, max_length=120)
    answers: Dict[str, int]          # { "0": 2, "1": 0, ... } — question_index: option_index
    questions: List[Dict[str, Any]] = []  # Legacy: client-supplied questions (ignored when quiz_token is valid)
    quiz_token: Optional[str] = None


class BreakdownItem(BaseModel):
    question: str
    user_answer: Optional[str]
    correct_answer: str
    correct: bool


class QuizSubmitResponse(BaseModel):
    topic: str
    score: int
    total: int
    level: str
    breakdown: List[BreakdownItem]


# ─── Content ───

class ContentGenerateRequest(BaseModel):
    topic: str = Field(..., min_length=2, max_length=120)
    level: str = Field(..., pattern="^(Beginner|Intermediate|Expert)$")
    score: int = Field(..., ge=0, le=10)


class ContentStreamRequest(ContentGenerateRequest):
    # Regenerate only these sections (e.g. ones reported in incomplete_sections). Omit for everything.
    sections: Optional[List[str]] = Field(default=None, max_length=10)
    # Content history: continue this saved session, or start a deliberately separate one. Default: reuse the user's session for the topic+level.
    session_id: Optional[str] = Field(default=None, max_length=36)
    new_session: bool = False
    # The completed Diagnostic Study session this material is built from: its concept results decide what is deepened and what is not re-taught.
    diagnostic_session_id: Optional[str] = Field(default=None, max_length=36)

    @field_validator("sections")
    @classmethod
    def _known(cls, v):
        from services.content_generation import SECTION_NAMES
        if v is not None:
            bad = [x for x in v if x not in SECTION_NAMES]
            if bad or not v:
                raise ValueError(f"unknown or empty sections: {bad}")
        return v


class Flashcard(BaseModel):
    question: str
    answer: str


class Recommendations(BaseModel):
    weak_areas: List[str] = []
    next_topics: List[Any] = []
    practice_suggestions: List[str] = []
    resources: List[Any] = []


class ContentGenerateResponse(BaseModel):
    # Echoed from the request so the frontend doesn't need extra state
    topic: str
    level: str
    # 7 structured content sections
    summary: str
    key_concepts: Any                   # List[{term, definition}] or raw string
    deep_dive: str
    real_world_applications: str
    viva_voice: str
    best_practices: str                 # deprecated alias of practical_guide (kept for existing clients)
    practical_guide: Optional[str] = None
    learning_roadmap: str
    # Interactive components
    flashcards: List[Flashcard]
    recommendations: Recommendations
    # AI tutor personalised greeting
    agent_intro: Optional[str] = None
    # Sections that could not be generated (they hold a placeholder); regenerate them via the stream endpoint
    incomplete_sections: List[str] = []


# ─── History ───

class HistoryItem(BaseModel):
    id: str
    topic: str
    score: int
    level: str
    created_at: str


class HistoryResponse(BaseModel):
    history: List[HistoryItem]


# ─── Agent Chat ───

class AgentChatMessage(BaseModel):
    role: str   # 'user' | 'assistant'
    content: str


class AgentChatRequest(BaseModel):
    topic: str
    level: str
    message: str = Field(..., min_length=1, max_length=1000)
    history: List[AgentChatMessage] = []


class AgentChatResponse(BaseModel):
    reply: str
