"""Deterministic, versioned, explainable proficiency estimation (0-5 scale).

Model (PROFICIENCY_VERSION = "prof-v1"):
  implied level of one piece of evidence  L_i = clamp(difficulty_i * score_i, 0, max_level)
      (a perfect result at difficulty d demonstrates level d; 50% at d demonstrates d/2)
  weight                                   w_i = kind_weight * 0.5 ** (age_days / half_life) * reliability_i
      reliability_i = min(1, n_items_i / reference_items)  (a 1-question quiz counts less than a 10-question one)
  proficiency = sum(w_i * L_i) / sum(w_i)
  confidence  = (1 - exp(-sum(w_i)/k)) * (1 - 0.5 * normalised_stdev)   -> 0..1
LLMs never set these numbers: they can only produce the raw evidence (e.g. rubric judgements) upstream.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

PROFICIENCY_VERSION = "prof-v1"


class EvidenceKind(str, Enum):
    QUIZ = "quiz"
    EXERCISE = "exercise"
    CODING = "coding"
    PROJECT = "project"
    SELF_REPORT = "self_report"


class Evidence(BaseModel):
    skill_id: str
    kind: EvidenceKind
    score: float = Field(ge=0, le=1)  # fraction of available credit earned
    difficulty: float = Field(ge=0, le=5)
    n_items: int = Field(1, ge=1)
    occurred_at: datetime
    source_id: str | None = None  # attempt / project id for traceability


class ProficiencyConfig(BaseModel):
    max_level: float = 5.0
    half_life_days: float = 90.0
    reference_items: int = 5
    confidence_k: float = 2.0
    kind_weights: dict[EvidenceKind, float] = {
        EvidenceKind.QUIZ: 1.0, EvidenceKind.EXERCISE: 1.3, EvidenceKind.CODING: 1.6,
        EvidenceKind.PROJECT: 2.0, EvidenceKind.SELF_REPORT: 0.2,
    }


class Contribution(BaseModel):
    source_id: str | None
    kind: EvidenceKind
    implied_level: float
    weight: float
    share: float  # fraction of total weight


class ProficiencyEstimate(BaseModel):
    skill_id: str
    proficiency_score: float
    confidence_score: float
    evidence_count: int
    last_assessed_at: datetime | None
    proficiency_version: str = PROFICIENCY_VERSION
    explanation: list[Contribution] = []


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def estimate(skill_id: str, evidence: list[Evidence], now: datetime,
             cfg: ProficiencyConfig = ProficiencyConfig()) -> ProficiencyEstimate:
    items = [e for e in evidence if e.skill_id == skill_id]
    if not items:
        return ProficiencyEstimate(skill_id=skill_id, proficiency_score=0.0, confidence_score=0.0,
                                   evidence_count=0, last_assessed_at=None)
    now = _aware(now)
    rows = []
    for e in items:
        age = max(0.0, (now - _aware(e.occurred_at)).total_seconds() / 86400)
        level = min(cfg.max_level, max(0.0, e.difficulty * e.score))
        w = (cfg.kind_weights[e.kind] * 0.5 ** (age / cfg.half_life_days)
             * min(1.0, e.n_items / cfg.reference_items))
        rows.append((e, level, w))
    total_w = sum(w for _, _, w in rows)
    if total_w <= 0:
        return ProficiencyEstimate(skill_id=skill_id, proficiency_score=0.0, confidence_score=0.0,
                                   evidence_count=len(items), last_assessed_at=max(_aware(e.occurred_at) for e in items))
    score = sum(w * l for _, l, w in rows) / total_w
    var = sum(w * (l - score) ** 2 for _, l, w in rows) / total_w
    spread = min(1.0, math.sqrt(var) / (cfg.max_level / 2))
    conf = (1 - math.exp(-total_w / cfg.confidence_k)) * (1 - 0.5 * spread)
    return ProficiencyEstimate(
        skill_id=skill_id, proficiency_score=round(score, 3), confidence_score=round(max(0.0, min(1.0, conf)), 3),
        evidence_count=len(items), last_assessed_at=max(_aware(e.occurred_at) for e, _, _ in rows),
        explanation=[Contribution(source_id=e.source_id, kind=e.kind, implied_level=round(l, 3), weight=round(w, 4),
                                  share=round(w / total_w, 3)) for e, l, w in rows])


def band(score: float) -> str:
    """Human label for legacy UI compatibility only; the numeric score is the source of truth."""
    return "Beginner" if score < 2 else "Intermediate" if score < 3.75 else "Expert"
