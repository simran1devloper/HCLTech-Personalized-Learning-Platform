"""Question bank persistence, seeding, retrieval and LLM top-up (SQLite and, when reachable, PostgreSQL)."""
import asyncio
import json
import random
from collections import Counter

import pytest

from app.core.container import get_container, reset_container
from app.domains.assessments.bank import generator as gen
from app.domains.assessments.bank.seeding import EXPECTED, seed_question_bank
from app.domains.assessments.bank.service import QuestionBankService, _bucket
from app.domains.skill_registry.seed import seed
from app.domains.skill_registry.service import SkillRegistry
from app.infrastructure.database import tables as T
from app.infrastructure.database.assessment_store import AssessmentStore
from app.infrastructure.database.skill_repository import SqlSkillRepository


@pytest.fixture
def env(db):
    reg = SkillRegistry(SqlSkillRepository(db))
    seed(reg)
    store = AssessmentStore(db)
    return reg, store, QuestionBankService(store)


def py(reg):
    return reg.resolve("Python", 1.0).skill_id


def test_seeding_inserts_everything_once_and_is_idempotent(env):
    reg, store, _ = env
    first = seed_question_bank(store, reg)
    assert first["inserted"] == EXPECTED == 186 and first["invalid"] == [] and first["missing_skills"] == []
    again = seed_question_bank(store, reg)
    assert again["inserted"] == 0 and store.count_by_source("seed") == EXPECTED
    forced = seed_question_bank(store, reg, skills=["Python", "SQL"])               # explicit incremental run re-checks rows: still no duplicates
    assert forced["inserted"] == 0 and forced["existing"] == 18 + 12 and store.count_by_source("seed") == EXPECTED
    assert seed_question_bank(store, reg, skills=["Redis"])["inserted"] == 0


def test_seed_ids_are_deterministic_so_a_wipe_and_reseed_reproduces_them(env):
    reg, store, _ = env
    seed_question_bank(store, reg)
    ids = lambda: sorted(q["id"] for q in store.candidates(py(reg)))  # noqa: E731
    before = ids()
    with store.db.begin() as c:
        c.execute(T.assessment_question_bank.delete())
    assert ids() == [] and seed_question_bank(store, reg)["inserted"] == EXPECTED
    assert ids() == before and len(before) == 18


def test_new_skill_questions_can_be_added_without_code_changes(env):
    reg, store, _ = env
    skill = reg.resolve("Kafka", 1.0)
    q = dict(concept="Partitions", subtopic="Partitions", difficulty=2, question_type="single_choice", cognitive_level="understand", question_text="What does a Kafka partition provide?",
             options=["Ordering within the partition", "Global ordering across topics", "Encryption", "Schema validation"], correct=[0], explanation="Records in a partition are strictly ordered.")
    from app.domains.assessments.bank.validation import content_hash
    h = content_hash(q["question_text"], None)
    assert store.insert_question(skill.skill_id, q, content_hash=h, source_type="manual", validation_status="validated", quality=0.7)[1] is True
    assert store.insert_question(skill.skill_id, q, content_hash=h, source_type="manual", validation_status="validated", quality=0.7)[1] is False
    assert [c["concept"] for c in store.candidates(skill.skill_id)] == ["Partitions"]


def test_selection_uses_only_active_validated_questions(env):
    reg, store, svc = env
    seed_question_bank(store, reg)
    sid = py(reg)
    victim = store.candidates(sid)[0]["id"]
    store.set_status(victim, validation_status="deprecated")
    assert victim not in {c["id"] for c in store.candidates(sid)} and len(store.candidates(sid)) == 17
    other = store.candidates(sid)[0]["id"]
    store.set_status(other, validation_status="rejected")
    store.set_status(store.candidates(sid)[0]["id"], is_active=False)
    assert len(store.candidates(sid)) == 15
    sel = svc.select("u1", sid, 10, None, random.Random(1))
    assert len(sel.rows) == 10 and not {victim, other} & {r["id"] for r in sel.rows}
    assert len(svc.select("u1", sid, 20, None, random.Random(1)).rows) == 15 and svc.select("u1", sid, 20, None, random.Random(1)).shortfall == 5


def test_ten_question_selection_matches_distribution_and_covers_many_concepts(env):
    reg, store, svc = env
    seed_question_bank(store, reg)
    for seed_ in range(8):
        sel = svc.select("u1", py(reg), 10, None, random.Random(seed_))
        assert len(sel.rows) == 10 and len({r["id"] for r in sel.rows}) == 10 and sel.shortfall == 0
        concepts = Counter(r["concept"] for r in sel.rows)
        assert len(concepts) == 5 and min(concepts.values()) >= 1 and max(concepts.values()) <= 3, concepts   # every concept area covered, none dominates
        assert len({r["subtopic"] for r in sel.rows}) >= 6                                                    # and many different sub-topics
        buckets = Counter(_bucket(r["difficulty"]) for r in sel.rows)
        assert buckets["advanced"] >= 2 and buckets["foundational"] >= 3 and buckets["intermediate"] >= 3
        assert any(r["question_type"] in ("scenario", "practical", "debugging") for r in sel.rows)


def test_selection_is_varied_but_reproducible_for_a_seed(env):
    reg, store, svc = env
    seed_question_bank(store, reg)
    ids = lambda s: [r["id"] for r in svc.select("u1", py(reg), 8, None, random.Random(s)).rows]  # noqa: E731
    assert ids(5) == ids(5) and len({tuple(ids(s)) for s in range(6)}) > 1


def test_recently_seen_questions_are_avoided_but_never_cause_a_failure(env):
    reg, store, svc = env
    seed_question_bank(store, reg)
    sid = py(reg)
    first = svc.start("u1", sid, 6, None, "k1")[0]
    seen1 = {r["question_id"] for r in store.attempt_questions(first)}
    second = svc.start("u1", sid, 6, None, "k2")[0]
    seen2 = {r["question_id"] for r in store.attempt_questions(second)}
    assert not seen1 & seen2                                                            # second attempt is fully fresh
    third = svc.start("u1", sid, 6, None, "k3")[0]
    seen3 = {r["question_id"] for r in store.attempt_questions(third)}
    assert not seen3 & (seen1 | seen2)                                                  # 18 questions, three attempts of 6: still no repeats
    fourth = svc.start("u1", sid, 6, None, "k4")[0]                                     # bank exhausted: repeats are allowed, the attempt still starts
    assert len(store.attempt_questions(fourth)) == 6
    assert svc.start("someone-else", sid, 6, None, "k1")[0] != first                    # the key is per user


def test_options_are_shuffled_per_attempt_and_correct_indices_follow(env):
    reg, store, svc = env
    seed_question_bank(store, reg)
    aid = svc.start("u1", py(reg), 10, None, None)[0]
    rows = store.attempt_questions(aid)
    full = store.full([r["question_id"] for r in rows])
    moved = 0
    for r in rows:
        s, bank = r["question_snapshot"], full[r["question_id"]]
        assert sorted(s["options"]) == sorted(bank["options"])
        assert sorted(s["options"][i] for i in s["correct"]) == sorted(bank["options"][i] for i in bank["correct_answer"])
        moved += s["options"] != bank["options"]
    assert moved >= 5


def test_attempt_snapshots_survive_bank_changes(env):
    reg, store, svc = env
    seed_question_bank(store, reg)
    aid = svc.start("u1", py(reg), 5, None, None)[0]
    before = [r["question_snapshot"] for r in store.attempt_questions(aid)]
    for r in before:
        store.set_status(r["question_id"], validation_status="deprecated")
    from sqlalchemy import update
    with store.db.begin() as c:
        c.execute(update(T.assessment_question_bank).values(question_text="CHANGED", explanation="CHANGED"))
    assert [r["question_snapshot"] for r in store.attempt_questions(aid)] == before and all(s["prompt"] != "CHANGED" for s in before)


def test_attempt_creation_is_one_transaction_and_idempotent(env):
    reg, store, svc = env
    seed_question_bank(store, reg)
    sid = py(reg)
    a1, c1, *_ = svc.start("u1", sid, 5, None, "same-key")
    a2, c2, *_ = svc.start("u1", sid, 5, None, "same-key")
    assert a1 == a2 and c1 is True and c2 is False
    assert len(store.attempt_questions(a1)) == 5
    with store.db.begin() as c:
        n = c.execute(T.assessment_attempts.select().where(T.assessment_attempts.c.learner_id == "u1")).all()
    assert len(n) == 1


def test_question_cannot_be_assigned_twice_in_one_attempt(env):
    reg, store, svc = env
    seed_question_bank(store, reg)
    sid = py(reg)
    snaps = [svc.snapshot(store.full([c["id"]])[c["id"]], random.Random(0)) for c in store.candidates(sid)[:2]]
    snaps[1] = {**snaps[1], "question_id": snaps[0]["question_id"]}
    with pytest.raises(Exception):
        store.create_attempt(attempt_id="dup-att", user_id="u1", skill_id=sid, questions=snaps, difficulty_level="x", idempotency_key=None)
    assert store.attempt_row("u1", "dup-att") is None                                    # rolled back as a whole


# ------------------------------------------------------------------------------------------------ LLM fallback
def fake_json(n, base="Generated", concept="Generics"):
    return json.dumps([{"concept": f"{concept} {i}", "difficulty": 2 + (i % 3), "question_type": "single_choice", "cognitive_level": "apply",
                        "question_text": f"{base} question number {i} about this skill, which statement is accurate?", "code_snippet": None,
                        "options": [f"right {i}", f"wrong a {i}", f"wrong b {i}", f"wrong c {i}"], "correct": [0], "explanation": f"Option right {i} is the accurate statement."} for i in range(n)])


class FakeCall:
    def __init__(self, payload=None):
        self.prompts, self.payload = [], payload

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        n = int(prompt.split("exactly ")[1].split(" ")[0])
        return (self.payload if self.payload is not None else fake_json(n)), 100, 200


def test_top_up_generates_only_missing_validates_and_persists(env):
    reg, store, _ = env
    seed_question_bank(store, reg)
    call = FakeCall()
    svc = QuestionBankService(store, call)
    sid = py(reg)
    sel = svc.select("u1", sid, 20, None, random.Random(1))
    assert len(sel.rows) == 18 and sel.shortfall == 2
    added = asyncio.run(svc.top_up("Python", sid, sel.shortfall))
    assert added == 2 and len(call.prompts) == 1 and "exactly 2 questions" in call.prompts[0]
    assert len(store.candidates(sid)) == 20
    sel2 = svc.select("u1", sid, 20, None, random.Random(2))
    assert len(sel2.rows) == 20 and sel2.shortfall == 0 and len(call.prompts) == 1       # next assessment: no model call at all


def test_invalid_generated_questions_are_rejected_not_served(env):
    reg, store, _ = env
    seed_question_bank(store, reg)
    bad = json.dumps([{"concept": "X", "difficulty": 2, "question_type": "single_choice", "cognitive_level": "apply", "question_text": "A question with no valid answer index?",
                       "options": ["a", "b", "c", "d"], "correct": [9], "explanation": "Explained here at length."},
                      {"concept": "Y", "difficulty": 2, "question_type": "single_choice", "cognitive_level": "apply", "question_text": "A question whose options repeat themselves?",
                       "options": ["same", "same", "other", "more"], "correct": [0], "explanation": "Explained here at length."}, "junk", 7])
    svc = QuestionBankService(store, FakeCall(bad))
    sid = py(reg)
    assert asyncio.run(svc.top_up("Python", sid, 3)) == 0
    assert len(store.candidates(sid)) == 18                                               # nothing invalid entered the active bank


def test_malformed_model_output_and_provider_errors_never_raise(env):
    reg, store, _ = env
    seed_question_bank(store, reg)
    for payload in ("not json at all", "{}", "[]"):
        assert asyncio.run(QuestionBankService(store, FakeCall(payload)).top_up("Python", py(reg), 2)) == 0

    async def boom(prompt):
        raise RuntimeError("provider down")
    assert asyncio.run(QuestionBankService(store, boom).top_up("Python", py(reg), 2)) == 0

    async def slow(prompt):
        await asyncio.sleep(5)
    assert asyncio.run(QuestionBankService(store, slow).top_up("Python", py(reg), 2, timeout_s=0.05)) == 0


def test_duplicate_generated_questions_are_not_inserted_twice(env):
    reg, store, _ = env
    seed_question_bank(store, reg)
    svc = QuestionBankService(store, FakeCall())
    assert asyncio.run(svc.top_up("Python", py(reg), 2)) == 2
    assert asyncio.run(svc.top_up("Python", py(reg), 2)) == 0                             # identical output -> same content hashes -> ignored
    assert len(store.candidates(py(reg))) == 20


def test_availability_is_cached_and_invalidated_on_change(env):
    reg, store, svc = env
    seed_question_bank(store, reg)
    a = svc.availability()
    assert a[py(reg)]["total"] == 18 and a[py(reg)]["concepts"] == 5 and set(a[py(reg)]["by_difficulty"]) == {1, 2, 3, 4}
    store.set_status(store.candidates(py(reg))[0]["id"], validation_status="deprecated")
    assert svc.availability()[py(reg)]["total"] == 18                                      # served from the cache within the TTL
    svc.invalidate()
    assert svc.availability()[py(reg)]["total"] == 17
