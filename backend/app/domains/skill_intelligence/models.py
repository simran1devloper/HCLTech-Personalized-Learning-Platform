from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.domains.skill_intelligence.mastery import CareerMetrics, SkillStatus

STAGES = ["Foundations", "Core Skills", "Applied Skills", "Advanced Skills", "Career Specialization"]


class GraphNode(BaseModel):
    skill_id: str
    slug: str
    name: str
    category: str
    difficulty: int
    estimated_hours: float
    description: str = ""
    status: SkillStatus
    is_target: bool = False            # a requirement of the career (vs. a prerequisite pulled in by the dependency graph)
    importance: float = 0.0
    critical: bool = False
    target: float                      # level required (career target, or the strictest level a dependent needs)
    proficiency: float                 # validated, 0..5
    confidence: float
    evidence_count: int
    mastery: float                     # 0..1 (attainment x reliability)
    learning_progress: float           # 0..1, never counted as mastery
    revision_reason: str | None = None
    missing_prerequisites: list[str]   # transitive, in learning order
    prerequisites: list[str]           # direct, within the scope
    unlocks: int                       # direct dependents within the scope
    depth: int                         # longest prerequisite chain below this skill
    stage: int                         # index into STAGES
    hours_remaining: float


class GraphEdge(BaseModel):
    source: str                        # prerequisite
    target: str                        # dependent
    min_proficiency: float
    satisfied: bool                    # prerequisite's validated proficiency >= min_proficiency


class RoleInfo(BaseModel):
    slug: str
    title: str
    description: str = ""


class CareerSkillGraph(BaseModel):
    role: RoleInfo | None
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    metrics: CareerMetrics
    categories: list[str]
    stages: list[str] = STAGES
    recommendations: list["Recommendation"]
    generated_at: datetime


Priority = Literal["high", "medium", "low"]
Action = Literal["revise", "learn_prerequisites", "start_learning", "continue_learning", "take_assessment"]


class Recommendation(BaseModel):
    skill_id: str
    name: str
    rank: int
    priority: Priority
    score: float
    explanation: str
    missing_prerequisites: list[str]   # skill ids, learning order
    unlocks: list[str]                 # skill ids that become learnable once this one is mastered
    suggested_action: Action
    next_skill_id: str | None = None   # for learn_prerequisites: the prerequisite to start with
    drivers: dict[str, float]


CareerSkillGraph.model_rebuild()
