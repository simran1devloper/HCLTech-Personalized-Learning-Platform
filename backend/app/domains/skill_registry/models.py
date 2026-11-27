from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SkillStatus(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    PROPOSED = "proposed"  # discovered, awaiting review


class Skill(BaseModel):
    skill_id: str = Field(default_factory=lambda: str(uuid4()))
    slug: str  # normalized unique key
    name: str
    description: str = ""
    category: str = "general"
    domain: str = "software"
    difficulty: int = Field(2, ge=1, le=5)
    aliases: list[str] = []
    parent_skill_id: str | None = None
    status: SkillStatus = SkillStatus.ACTIVE
    estimated_hours: float = Field(10.0, gt=0)  # learning effort, used by roadmap/gap prioritisation
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Dependency(BaseModel):
    """`skill_id` requires `prerequisite_id`."""
    skill_id: str
    prerequisite_id: str
    min_proficiency: float = Field(2.0, ge=0, le=5)  # prerequisite level considered "satisfied"
