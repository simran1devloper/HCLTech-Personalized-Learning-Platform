"""Adaptive question selection and attempt evidence aggregation."""
from __future__ import annotations

from datetime import datetime

from app.domains.assessments.models import GradeResult, Question
from app.domains.learning_intelligence.proficiency import Evidence, EvidenceKind


def target_difficulty(prior_level: float | None, recent: list[tuple[int, float]]) -> int:
    """prior_level: current proficiency (0-5) or None. recent: [(difficulty, earned)] this session, oldest first.
    Staircase: start near prior, step up after a success (>=0.8), down after a failure (<=0.4)."""
    d = round(prior_level) if prior_level is not None else 2
    d = max(1, min(5, d if d else 1))
    for diff, earned in recent:
        d = diff
        if earned >= 0.8:
            d += 1
        elif earned <= 0.4:
            d -= 1
        d = max(1, min(5, d))
    return d


def select_next(bank: list[Question], skill_id: str, *, prior_level: float | None,
                recent: list[tuple[int, float]], seen_ids: set[str], recently_seen_prompts: set[str] = frozenset(),
                prefer_practical: bool = False) -> Question | None:
    want = target_difficulty(prior_level, recent)
    pool = [q for q in bank if q.skill_id == skill_id and q.question_id not in seen_ids
            and q.prompt not in recently_seen_prompts]
    if not pool:
        return None
    # closest difficulty, then practical preference, then stable id order (deterministic)
    return min(pool, key=lambda q: (abs(q.difficulty - want), 0 if (q.practical == prefer_practical) else 1,
                                    q.question_id))


def attempt_to_evidence(questions: dict[str, Question], grades: list[GradeResult], when: datetime,
                        attempt_id: str) -> list[Evidence]:
    """One Evidence per (skill, practical?) group. Items needing review are excluded, so an
    attempt with only unreviewed items yields no evidence."""
    groups: dict[tuple[str, bool], list[tuple[Question, GradeResult]]] = {}
    for g in grades:
        q = questions[g.question_id]
        if g.needs_review:
            continue
        groups.setdefault((q.skill_id, q.practical), []).append((q, g))
    out = []
    for (skill_id, practical), rows in groups.items():
        # difficulty-weighted mean credit; implied difficulty = mean difficulty of the items
        total_d = sum(q.difficulty for q, _ in rows)
        score = sum(q.difficulty * g.earned for q, g in rows) / total_d
        out.append(Evidence(skill_id=skill_id, kind=EvidenceKind.EXERCISE if practical else EvidenceKind.QUIZ,
                            score=round(score, 4), difficulty=round(total_d / len(rows), 2), n_items=len(rows),
                            occurred_at=when, source_id=attempt_id))
    return out
