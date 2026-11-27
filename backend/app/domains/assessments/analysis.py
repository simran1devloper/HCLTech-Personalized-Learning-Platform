"""Deterministic scoring and skill-gap analysis. Pure functions: no database, no model, fully unit-testable.

Thresholds (accuracy):  >=85% strong | 70-84% good | 50-69% developing | <50% needs improvement.
A concept with fewer than MIN_ITEMS assessed questions is **unknown**, never "strong" or "weak": too little evidence.
A *critical gap* is a concept with enough evidence and accuracy below CRITICAL (25%)."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

MIN_ITEMS = 2
CRITICAL = 0.25
STRONG, GOOD, DEVELOPING = 0.85, 0.70, 0.50
LEVELS = ("strong", "good", "developing", "needs_improvement", "unknown")
SAMPLE_CAVEAT = "A short assessment samples only part of a skill; treat concept results with fewer than {n} questions as indicative, not conclusive."


def level_for(accuracy: float, attempted: int) -> str:
    if attempted < MIN_ITEMS:
        return "unknown"
    return "strong" if accuracy >= STRONG else "good" if accuracy >= GOOD else "developing" if accuracy >= DEVELOPING else "needs_improvement"


def overall_level(score_pct: float) -> str:
    return level_for(score_pct / 100, MIN_ITEMS)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "concept"


def concept_results(items: list[dict]) -> list[dict]:
    """items: [{concept, earned (0..1)}] -> one row per concept with accuracy and level. ``correct_answers`` is the sum of earned credit
    (fractional for partially correct multiple-select answers)."""
    g: dict[str, list[float]] = defaultdict(list)
    for it in items:
        g[it["concept"]].append(float(it["earned"]))
    out = []
    for concept, vals in g.items():
        n, got = len(vals), sum(vals)
        acc = got / n
        lvl = level_for(acc, n)
        out.append({"concept": concept, "questions_attempted": n, "correct_answers": round(got, 3), "accuracy": round(acc, 4), "proficiency_level": lvl,
                    "identified_gap": lvl == "needs_improvement"})
    return sorted(out, key=lambda r: (r["accuracy"], r["concept"]))


def breakdown(items: list[dict], key: str) -> list[dict]:
    """Accuracy grouped by an item attribute (difficulty, question_type, cognitive_level, subtopic)."""
    g: dict[Any, list[float]] = defaultdict(list)
    for it in items:
        g[it[key]].append(float(it["earned"]))
    return [{key: k, "questions": len(v), "accuracy": round(sum(v) / len(v), 4)} for k, v in sorted(g.items(), key=lambda kv: str(kv[0]))]


def gap_report(skill: str, skill_id: str, items: list[dict], concepts: list[dict], prerequisite_gaps: list[dict] | None = None) -> dict:
    """Structured skill-gap report. ``items`` carries per-question concept, difficulty, earned; ``prerequisite_gaps`` comes from the skill graph."""
    n = len(items)
    score = round(100 * sum(float(i["earned"]) for i in items) / n, 1) if n else None
    by = {c["concept"]: c for c in concepts}
    min_diff: dict[str, int] = {}
    for it in items:
        min_diff[it["concept"]] = min(min_diff.get(it["concept"], 9), int(it["difficulty"]))
    strong = [c["concept"] for c in concepts if c["proficiency_level"] in ("strong", "good")]
    unknown = [c["concept"] for c in concepts if c["proficiency_level"] == "unknown"]
    critical = [c["concept"] for c in concepts if c["proficiency_level"] != "unknown" and c["accuracy"] < CRITICAL]
    weak = [c["concept"] for c in concepts if c["identified_gap"] and c["concept"] not in critical]
    # learn foundational concepts first (lowest difficulty seen), the most-missed first within a difficulty
    gaps = critical + weak
    order = sorted(gaps, key=lambda k: (min_diff.get(k, 9), by[k]["accuracy"], k))
    return {
        "skill": skill, "skill_id": skill_id, "overall_score": score, "overall_level": overall_level(score) if score is not None else "unknown",
        "questions": n, "strong_concepts": strong, "weak_concepts": weak, "critical_gaps": critical, "unknown_concepts": unknown,
        "recommended_sections": [{"concept": k, "key": f"gap-{slug(k)}", "priority": "critical" if k in critical else "high", "accuracy": by[k]["accuracy"]} for k in order],
        "recommended_learning_order": order,
        "prerequisite_gaps": prerequisite_gaps or [],
        "by_difficulty": breakdown(items, "difficulty"), "by_question_type": breakdown(items, "question_type"), "by_cognitive_level": breakdown(items, "cognitive_level"),
        "concepts": concepts,
        "caveat": SAMPLE_CAVEAT.format(n=MIN_ITEMS) + (f" This attempt had {n} question{'s' if n != 1 else ''}." if n < 10 else ""),
        "basis": "deterministic scoring of your saved answers; no AI model was used",
    }

