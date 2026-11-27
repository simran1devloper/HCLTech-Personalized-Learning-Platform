"""Idempotent seeding of the authored question bank (``python -m app.domains.assessments.bank.seeding``).

Deterministic ids (uuid5 of skill + content hash) and the UNIQUE(skill_id, content_hash) constraint make re-runs no-ops; new skills/questions are added
incrementally just by editing ``seed_data.py``. Every question is validated before insertion; invalid ones are reported, never inserted."""
from __future__ import annotations

import logging
import uuid

from app.domains.assessments.bank.seed_data import SEED
from app.domains.assessments.bank.validation import content_hash, quality_score, validate_question
from app.infrastructure.database.assessment_store import AssessmentStore

log = logging.getLogger("question_seed")
NS = uuid.UUID("6f1f6a52-0a3b-4a8c-9f0e-2d6b0a5c1e11")
PYTHON_SKILLS = {"Python", "Data Structures and Algorithms"}
EXPECTED = sum(len(v) for v in SEED.values())


def seed_question_bank(store: AssessmentStore, registry, skills: list[str] | None = None) -> dict:
    out = {"inserted": 0, "existing": 0, "invalid": [], "missing_skills": []}
    if not skills and store.count_by_source("seed") >= EXPECTED:   # fast path on every startup: one COUNT, no inserts
        out["existing"] = EXPECTED
        return out
    for name, items in SEED.items():
        if skills and name not in skills:
            continue
        skill = registry.resolve(name, 1.0)
        if not skill:
            out["missing_skills"].append(name)
            continue
        for q in items:
            q = {**q, "code_language": "python" if (q.get("code_snippet") and name in PYTHON_SKILLS) else None}
            errs = validate_question(q)
            if errs:
                out["invalid"].append((name, q["question_text"][:60], errs))
                continue
            h = content_hash(q["question_text"], q["code_snippet"])
            _, created = store.insert_question(skill.skill_id, q, content_hash=h, source_type="seed", validation_status="validated", quality=quality_score(q),
                                               question_id=str(uuid.uuid5(NS, f"{skill.skill_id}:{h}")))
            out["inserted" if created else "existing"] += 1
    if out["invalid"]:
        log.error("seed questions failed validation: %s", out["invalid"])
    return out


if __name__ == "__main__":  # pragma: no cover
    import json
    from app.core.container import get_container
    c = get_container()
    print(json.dumps(seed_question_bank(c.assessments, c.registry, None) | {"invalid": []}, default=str))
