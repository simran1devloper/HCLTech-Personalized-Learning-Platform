from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.domains.skill_registry.models import utcnow


class ItemStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED_MASTERED = "skipped_mastered"
    BLOCKED = "blocked"  # derived on evaluation, persisted for visibility
    SUPERSEDED = "superseded"  # removed by a replan (kept for history)


class Milestone(BaseModel):
    item_id: str
    skill_id: str
    skill_name: str
    order: int
    kind: str = "learn"  # learn | review (reintroduced weak skill)
    from_level: float
    target_level: float
    prerequisites: list[str] = []  # skill_ids that must be satisfied first
    estimated_hours: float
    objectives: list[str]
    resource_ids: list[str] = []
    checkpoint: str  # assessment checkpoint description
    completion_criteria: dict  # machine-checkable: {"min_proficiency":x, "min_confidence":y}
    status: ItemStatus = ItemStatus.PENDING
    blocked_by: list[str] = []
    completed_at: datetime | None = None


class Roadmap(BaseModel):
    roadmap_id: str
    learner_id: str
    goal: str  # e.g. role slug
    version: int = 1
    hours_per_week: float = Field(5.0, gt=0)
    milestones: list[Milestone]
    total_hours: float = 0.0
    estimated_weeks: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)
    change_summary: list[str] = []
    generator: str = "roadmap-v1"


class RoadmapDiff(BaseModel):
    added: list[str] = []
    removed: list[str] = []
    skipped: list[str] = []
    reintroduced: list[str] = []
    unblocked: list[str] = []
    blocked: list[str] = []
    preserved_completed: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed or self.skipped or self.reintroduced or self.unblocked or self.blocked)
