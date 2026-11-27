"""Skill mastery, status and career readiness. Deterministic, versioned, no I/O, no LLM.

Three metrics are kept deliberately separate (MASTERY_VERSION = "mastery-v1"):

    coverage   = |skills with validated evidence| / |skills in the career map|
                 "How much of the map has been *assessed* at all."  (breadth; says nothing about how well)
    mastery_i  = attainment_i * reliability_i                                   (per skill, 0..1)
        attainment_i  = min(1, P_i / target_i)         P_i = validated proficiency (prof-v1, 0..5)
        reliability_i = floor + (1 - floor) * min(1, C_i / C_ref)    C_i = evidence confidence (prof-v1, 0..1)
                 "How well, discounted when the evidence is thin."
    readiness  = sum_i(importance_i * mastery_i) / sum_i(importance_i)         (over the role's *target* skills)
                 "Importance-weighted validated mastery."  Prerequisite-only skills carry no importance and do not
                 enter the readiness; they only gate statuses and recommendations.

Only *validated* evidence feeds P_i and C_i: self-reported evidence is excluded. Reading a lesson, or pressing a
"complete" button, produces no evidence, so it can never raise mastery or readiness; learning progress is tracked
separately and only ever moves a skill to "in progress".

A skill is **mastered** only when ALL hold:
    P_i >= target_i, C_i >= mastered_confidence, and at least one *graded assessment* backs the evidence
    (practical evidence submitted manually strengthens the estimate but cannot validate a skill on its own),
    and the skill is not due for revision.
It **needs revision** when it was demonstrated (P_i >= target_i) but either the newest evidence is older than
``stale_days`` or the newest evidence regressed well below the target while an earlier one met it.

Status precedence: needs_revision > mastered > (has evidence or learning progress ? blocked | in_progress)
                   > (prerequisites missing ? not_started : ready).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from app.domains.learning_intelligence.proficiency import Evidence, EvidenceKind, ProficiencyEstimate, estimate
from app.domains.learning_intelligence.skill_gap import Target

MASTERY_VERSION = "mastery-v1"
MANUAL_PREFIX = "manual:"  # source_id prefix of evidence submitted through the evidence API (not graded by the platform)


class SkillStatus(str, Enum):
    MASTERED = "mastered"
    IN_PROGRESS = "in_progress"
    READY = "ready"            # prerequisites satisfied, nothing started
    BLOCKED = "blocked"        # work started, but prerequisites are not satisfied yet
    NOT_STARTED = "not_started"  # nothing started and prerequisites are not satisfied yet
    NEEDS_REVISION = "needs_revision"


class MasteryConfig(BaseModel):
    mastered_confidence: float = Field(0.5, ge=0, le=1)
    confidence_ref: float = Field(0.6, gt=0, le=1)
    reliability_floor: float = Field(0.5, ge=0, le=1)
    stale_days: float = Field(90.0, gt=0)
    regression_ratio: float = Field(0.6, gt=0, le=1)
    default_target: float = Field(2.0, ge=0, le=5)  # required level for a skill that is only a prerequisite


class SkillMastery(BaseModel):
    skill_id: str
    target: float
    proficiency: float               # validated, 0..5
    confidence: float                # validated, 0..1
    evidence_count: int              # validated evidence items
    graded_evidence_count: int       # of which produced by graded assessments
    last_evidence_at: datetime | None
    attainment: float
    reliability: float
    mastery: float                   # attainment * reliability
    learning_progress: float         # 0..1, completed sections of the matching study material (NOT mastery)
    revision_reason: str | None = None  # 'stale' | 'regression'
    mastered: bool = False


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def validated(evidence: list[Evidence]) -> list[Evidence]:
    return [e for e in evidence if e.kind != EvidenceKind.SELF_REPORT]


def attainment(proficiency: float, target: float) -> float:
    return 1.0 if target <= 0 else max(0.0, min(1.0, proficiency / target))


def reliability(confidence: float, cfg: MasteryConfig = MasteryConfig()) -> float:
    return cfg.reliability_floor + (1 - cfg.reliability_floor) * max(0.0, min(1.0, confidence / cfg.confidence_ref))


def evaluate(skill_id: str, target: float, evidence: list[Evidence], now: datetime, learning_progress: float = 0.0,
             cfg: MasteryConfig = MasteryConfig()) -> SkillMastery:
    """`evidence` may contain other skills / self-reports: both are ignored here."""
    now = _aware(now)
    items = [e for e in validated(evidence) if e.skill_id == skill_id]
    est = estimate(skill_id, items, now)
    att = attainment(est.proficiency_score, target) if items else 0.0
    rel = reliability(est.confidence_score, cfg) if items else 0.0
    graded = sum(1 for e in items if not (e.source_id or "").startswith(MANUAL_PREFIX))
    last = max((_aware(e.occurred_at) for e in items), default=None)
    reason = None
    if items and est.proficiency_score >= target and last is not None and (now - last).days > cfg.stale_days:
        reason = "stale"
    elif len(items) >= 2:
        ordered = sorted(items, key=lambda e: _aware(e.occurred_at))
        lvl = lambda e: e.difficulty * e.score  # noqa: E731  (implied level of one item, as in prof-v1)
        if lvl(ordered[-1]) < cfg.regression_ratio * target and max(lvl(e) for e in ordered[:-1]) >= target:
            reason = "regression"
    mastered = (bool(items) and est.proficiency_score >= target and est.confidence_score >= cfg.mastered_confidence
                and graded >= 1 and reason is None)
    return SkillMastery(skill_id=skill_id, target=target, proficiency=est.proficiency_score, confidence=est.confidence_score,
                        evidence_count=len(items), graded_evidence_count=graded, last_evidence_at=last, attainment=round(att, 4),
                        reliability=round(rel, 4), mastery=round(att * rel, 4), learning_progress=round(max(0.0, min(1.0, learning_progress)), 3),
                        revision_reason=reason, mastered=mastered)


def status_of(m: SkillMastery, missing_prerequisites: bool) -> SkillStatus:
    if m.revision_reason:
        return SkillStatus.NEEDS_REVISION
    if m.mastered:
        return SkillStatus.MASTERED
    if m.evidence_count > 0 or m.learning_progress > 0:
        return SkillStatus.BLOCKED if missing_prerequisites else SkillStatus.IN_PROGRESS
    return SkillStatus.NOT_STARTED if missing_prerequisites else SkillStatus.READY


class CareerMetrics(BaseModel):
    skills_total: int
    skills_covered: int          # validated evidence exists
    mastered: int
    in_progress: int             # in_progress + blocked
    needs_revision: int
    remaining: int               # everything not mastered
    coverage: float              # 0..1
    mastery: float               # mean per-skill mastery over the map, 0..1
    readiness: float             # importance-weighted validated mastery over the targets, 0..1
    readiness_confidence: float  # importance-weighted evidence confidence over the targets, 0..1
    critical_gaps: list[str]     # skill ids of critical targets that are not mastered
    version: str = MASTERY_VERSION


def readiness_value(targets: list[Target], estimates: dict[str, ProficiencyEstimate], cfg: MasteryConfig = MasteryConfig()) -> tuple[float, float]:
    """(readiness, readiness_confidence) from per-skill estimates; shared with CareerService so the whole app agrees."""
    tw = sum(t.importance for t in targets) or 1.0
    r = c = 0.0
    for t in targets:
        e = estimates.get(t.skill_id)
        if e is None or e.evidence_count == 0:
            continue
        r += t.importance * attainment(e.proficiency_score, t.target_level) * reliability(e.confidence_score, cfg)
        c += t.importance * e.confidence_score
    return round(r / tw, 3), round(c / tw, 3)


def career_metrics(mastery: dict[str, SkillMastery], statuses: dict[str, SkillStatus], targets: list[Target],
                   cfg: MasteryConfig = MasteryConfig()) -> CareerMetrics:
    total = len(mastery)
    est = {sid: ProficiencyEstimate(skill_id=sid, proficiency_score=m.proficiency, confidence_score=m.confidence, evidence_count=m.evidence_count,
                                    last_assessed_at=m.last_evidence_at) for sid, m in mastery.items()}
    readiness, conf = readiness_value(targets, est, cfg)
    mastered = sum(s == SkillStatus.MASTERED for s in statuses.values())
    return CareerMetrics(
        skills_total=total, skills_covered=sum(m.evidence_count > 0 for m in mastery.values()), mastered=mastered,
        in_progress=sum(s in (SkillStatus.IN_PROGRESS, SkillStatus.BLOCKED) for s in statuses.values()),
        needs_revision=sum(s == SkillStatus.NEEDS_REVISION for s in statuses.values()), remaining=total - mastered,
        coverage=round(sum(m.evidence_count > 0 for m in mastery.values()) / total, 3) if total else 0.0,
        mastery=round(sum(m.mastery for m in mastery.values()) / total, 3) if total else 0.0,
        readiness=readiness, readiness_confidence=conf,
        critical_gaps=sorted(t.skill_id for t in targets if t.critical and statuses.get(t.skill_id) != SkillStatus.MASTERED))
