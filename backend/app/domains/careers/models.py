from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, Field


class SkillRequirement(BaseModel):
    skill_slug: str
    target_level: float = Field(ge=0, le=5)
    importance: float = Field(1.0, ge=0, le=1)
    critical: bool = False


class CareerRole(BaseModel):
    role_id: str = Field(default_factory=lambda: str(uuid4()))
    slug: str
    title: str
    description: str = ""
    requirements: list[SkillRequirement]
    # curated, NOT learner evidence; never mixed into the readiness score
    market_notes: str = "Curated requirement list; not a labour-market measurement."
