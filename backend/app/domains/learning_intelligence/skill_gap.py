"""Skill gap analysis with configurable, explainable prioritisation."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.domains.learning_intelligence.proficiency import ProficiencyEstimate
from app.domains.skill_registry.graph import SkillGraph
from app.domains.skill_registry.models import Skill


class Target(BaseModel):
    skill_id: str
    target_level: float = Field(ge=0, le=5)
    importance: float = Field(1.0, ge=0, le=1)  # career relevance
    critical: bool = False


class PriorityConfig(BaseModel):
    w_relevance: float = 0.30
    w_gap: float = 0.25
    w_unlocks: float = 0.20  # how many other target gaps depend on this skill
    w_uncertainty: float = 0.10  # low-confidence evidence -> verify/learn sooner
    w_effort: float = 0.15  # penalty for large effort (quick wins first)
    prereq_satisfied_level: float = 2.0
    max_effort_hours: float = 60.0


class SkillGapItem(BaseModel):
    skill_id: str
    name: str
    current: float
    target: float
    gap: float
    confidence: float
    missing_prerequisites: list[str]
    priority: float
    blocked: bool
    explanation: str


class SkillGapReport(BaseModel):
    items: list[SkillGapItem]  # sorted by priority desc (only skills with gap > 0)
    recommended_next: str | None
    config_used: PriorityConfig


def analyze(targets: list[Target], estimates: dict[str, ProficiencyEstimate], skills: dict[str, Skill],
            graph: SkillGraph, min_levels: dict[tuple[str, str], float] | None = None,
            cfg: PriorityConfig = PriorityConfig()) -> SkillGapReport:
    cur = {sid: e.proficiency_score for sid, e in estimates.items()}
    gaps = {t.skill_id: max(0.0, t.target_level - cur.get(t.skill_id, 0.0)) for t in targets}
    open_gap_ids = {s for s, g in gaps.items() if g > 0}
    items: list[SkillGapItem] = []
    for t in targets:
        gap = gaps[t.skill_id]
        if gap <= 0:
            continue
        sk = skills[t.skill_id]
        est = estimates.get(t.skill_id)
        conf = est.confidence_score if est else 0.0
        missing = graph.missing_prerequisites(t.skill_id, cur, min_levels, cfg.prereq_satisfied_level)
        unlocks = len(graph.transitive_dependents(t.skill_id) & open_gap_ids)
        unlock_norm = unlocks / max(1, len(open_gap_ids) - 1) if len(open_gap_ids) > 1 else 0.0
        effort = min(1.0, sk.estimated_hours * (gap / 5.0 + 0.2) / cfg.max_effort_hours)
        parts = {
            "relevance": cfg.w_relevance * t.importance,
            "gap": cfg.w_gap * gap / 5.0,
            "unlocks": cfg.w_unlocks * unlock_norm,
            "uncertainty": cfg.w_uncertainty * (1 - conf),
            "effort": -cfg.w_effort * effort,
        }
        prio = round(max(0.0, sum(parts.values())) + (0.05 if t.critical else 0.0), 4)
        names = [skills[m].name for m in missing if m in skills]
        expl = (f"{sk.name}: {cur.get(t.skill_id, 0):.1f}/{t.target_level:.1f} (confidence {conf:.0%}). "
                + "Drivers: " + ", ".join(f"{k} {v:+.2f}" for k, v in parts.items())
                + (f". Learn first: {', '.join(names)}." if names else "."))
        items.append(SkillGapItem(skill_id=t.skill_id, name=sk.name, current=round(cur.get(t.skill_id, 0.0), 2),
                                  target=t.target_level, gap=round(gap, 2), confidence=conf,
                                  missing_prerequisites=missing, priority=prio, blocked=bool(missing),
                                  explanation=expl))
    items.sort(key=lambda i: (-i.priority, i.name))
    rec = None
    ready = [i for i in items if not i.blocked]
    if ready:
        rec = ready[0].skill_id
    elif items:  # everything blocked: start at the earliest missing prerequisite of the top item
        rec = items[0].missing_prerequisites[0]
    return SkillGapReport(items=items, recommended_next=rec, config_used=cfg)
