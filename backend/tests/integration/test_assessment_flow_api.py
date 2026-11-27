"""Assessment journey over HTTP: Start -> answer (autosave) -> refresh -> submit -> results/skill gaps -> personalized learning plan.
Also the guarantees that make it cheap and safe: zero model calls on the hot paths, idempotency, ownership, snapshot-based review."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.container import get_container
from services import content_generation as cg
from tests.conftest import make_token
from tests.integration.test_assessment_bank_service import FakeCall, fake_json
from tests.integration.test_content_endpoints import sse

A = "/api/v1/assessments"
H = lambda u="learner-1", **extra: {"Authorization": f"Bearer {make_token(u)}", **extra}  # noqa: E731


class NoLLM:
    """Any model call in these tests is a failure unless a test opts in."""
    def __init__(self):
        self.calls = 0

    async def __call__(self, *a, **k):
        self.calls += 1
        raise AssertionError("the model must not be called on this path")


@pytest.fixture
def llm(client, monkeypatch):
    spy = NoLLM()
    get_container().bank.call = spy
    monkeypatch.setattr(cg, "_call_model", spy)
    return spy


def start(client, skill="Python", n=10, **kw):
    r = client.post(A, json={"skill": skill, "n_questions": n}, headers=H(**kw.pop("hdr", {})), **kw)
    return r


def key_of(aid):  # the server-side answer key (tests only)
    return {r["question_id"]: r["question_snapshot"] for r in get_container().assessments.attempt_questions(aid)}


def answer_all(client, aid, wrong=0, user="learner-1"):
    """Correct answers except for the first `wrong` questions."""
    key = key_of(aid)
    ans = {}
    for i, (qid, s) in enumerate(key.items()):
        right = s["correct"][0] if len(s["correct"]) == 1 else s["correct"]
        bad = [j for j in range(len(s["options"])) if j not in s["correct"]][0]
        ans[qid] = bad if i < wrong and len(s["correct"]) == 1 else ([bad] if i < wrong else right)
    return ans


# ------------------------------------------------------------------------------------------------ Start button workflow
def test_start_python_10_returns_a_persisted_attempt_with_ten_questions_and_no_answers(client, llm):
    r = start(client)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["skill"] == "Python" and d["total_questions"] == 10 and len(d["questions"]) == 10 and d["shortfall"] == 0 and d["note"] is None
    assert d["source"] == {"bank_questions": 10, "generated_inline": 0, "llm_calls": 0} and d["estimated_minutes"] >= 5
    assert len({q["question_id"] for q in d["questions"]}) == 10
    for q in d["questions"]:
        assert set(q) == {"question_id", "type", "kind", "prompt", "code_snippet", "options", "difficulty", "objective", "estimated_time_seconds"}   # no answer key, no explanation
        assert q["type"] in ("multiple_choice", "multiple_select") and len(q["options"]) >= 3
    assert llm.calls == 0
    again = client.get(f"{A}/{d['assessment_id']}", headers=H()).json()           # persisted: reopening needs nothing in memory
    assert again["status"] == "open" and [q["question_id"] for q in again["questions"]] == [q["question_id"] for q in d["questions"]] and again["answers"] == {}
    row = get_container().assessments.attempt_row("learner-1", d["assessment_id"])
    assert row["total_questions"] == 10 and row["difficulty_level"] in ("foundational", "intermediate", "advanced")


@pytest.mark.parametrize("skill", ["Python", "SQL", "Machine Learning", "Agentic AI", "FastAPI", "REST API Design", "Data Structures and Algorithms", "System Design", "RAG",
                                   "LangGraph", "MCP", "PostgreSQL", "Redis", "Docker", "Distributed Systems"])
def test_every_priority_skill_starts_instantly_from_the_bank(client, llm, skill):
    r = start(client, skill, 10)
    assert r.status_code == 201 and len(r.json()["questions"]) == 10 and r.json()["source"]["llm_calls"] == 0 and llm.calls == 0


def test_start_accepts_skill_aliases_and_reports_clear_errors(client, llm):
    assert start(client, "py", 3).json()["skill"] == "Python"
    nf = start(client, "Underwater Basket Weaving", 3)
    assert nf.status_code == 404 and nf.json()["error"]["message"] == "skill not found"
    assert client.post(A, json={"skill": "", "n_questions": 3}, headers=H()).status_code == 422
    assert client.post(A, json={"skill": "Python", "n_questions": 0}, headers=H()).status_code == 422
    assert client.post(A, json={"skill": "Python", "n_questions": 99}, headers=H()).status_code == 422
    assert client.post(A, json={"skill": "Python", "n_questions": 3}).status_code in (401, 403)


def test_double_click_and_concurrent_starts_with_one_key_create_exactly_one_attempt(client, llm):
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(lambda _: client.post(A, json={"skill": "Python", "n_questions": 5}, headers=H(**{"X-Idempotency-Key": "dbl"})), range(6)))
    ids = {r.json()["assessment_id"] for r in res}
    assert all(r.status_code == 201 for r in res) and len(ids) == 1
    assert client.get(A, headers=H()).json()["total"] == 1


def test_idempotency_survives_losing_the_in_memory_cache(client, llm):
    from app.core import security
    a = client.post(A, json={"skill": "Python", "n_questions": 5}, headers=H(**{"X-Idempotency-Key": "k-restart"})).json()
    security.idempotency._data.clear() if hasattr(security.idempotency, "_data") else None   # simulate a restart: only the database remembers the key
    for attr in vars(security.idempotency).values():
        if isinstance(attr, dict):
            attr.clear()
    b = client.post(A, json={"skill": "Python", "n_questions": 5}, headers=H(**{"X-Idempotency-Key": "k-restart"})).json()
    assert a["assessment_id"] == b["assessment_id"] and [q["question_id"] for q in a["questions"]] == [q["question_id"] for q in b["questions"]]


def wait_done(client, aid, timeout=20.0, hdr=None):
    """Poll the persisted generation state until it is no longer generating (the real UI follows the SSE stream; this is the same state)."""
    import time
    end = time.time() + timeout
    while time.time() < end:
        st = client.get(f"{A}/{aid}/generation", headers=H(**(hdr or {}))).json()
        if st["status"] != "generating":
            return st
        time.sleep(0.05)
    raise AssertionError("generation did not finish")


def test_empty_bank_skill_generates_validated_questions_then_serves_from_the_bank(live_client):
    client = live_client
    call = FakeCall()
    get_container().bank.call = call
    r = start(client, "JavaScript", 3)
    assert r.status_code == 202 and r.json()["status"] == "generating" and r.json()["requested"] == 3
    st = wait_done(client, r.json()["assessment_id"])
    assert st["status"] == "completed" and st["completed"] == 3 and st["progress"] == 100.0 and len(call.prompts) == 1
    r2 = start(client, "JavaScript", 3, hdr={"X-Idempotency-Key": "again"})     # now persisted and validated: no further model call
    assert r2.status_code == 201 and r2.json()["source"]["llm_calls"] == 0 and len(call.prompts) == 1 and len(r2.json()["questions"]) == 3


def test_empty_bank_and_failing_generator_ends_in_a_clear_error_event_and_leaves_no_history(live_client):
    client = live_client

    async def down(prompt):
        raise RuntimeError("provider down")
    get_container().bank.call = down
    r = start(client, "Kafka", 5)
    assert r.status_code == 202
    st = wait_done(client, r.json()["assessment_id"])
    assert st["status"] == "failed" and st["error"] == "provider" and st["completed"] == 0
    err = [e for e in client.get(f"{A}/{r.json()['assessment_id']}/events", headers=H()).text.split("\n\n") if e.startswith("id:") and "event: error" in e]
    assert len(err) == 1 and "AI service is unavailable" in err[0] and "provider down" not in err[0]
    assert client.get(A, headers=H()).json()["total"] == 0                          # nothing saved: not in the learner's history


def test_short_bank_serves_what_exists_at_once_and_generates_only_the_missing_questions(live_client):
    client = live_client
    call = FakeCall()
    get_container().bank.call = call
    r = start(client, "Python", 20)                                                  # bank holds 18 Python questions
    d = r.json()
    assert r.status_code == 202 and d["total_questions"] == 18 and d["requested"] == 20 and d["status"] == "generating"
    st = wait_done(client, d["assessment_id"])
    assert st["status"] == "completed" and st["completed"] == 20
    assert len(call.prompts) == 1 and "exactly 2 questions" in call.prompts[0]      # only the missing 2
    again = start(client, "Python", 20, hdr={"X-Idempotency-Key": "second"})
    assert again.status_code == 201 and again.json()["total_questions"] == 20 and len(call.prompts) == 1   # bank now complete: no model call


# ------------------------------------------------------------------------------------------------ answers, refresh, submit
def test_answers_are_saved_as_you_go_and_restored_after_refresh(client, llm):
    d = start(client, n=5).json(); aid = d["assessment_id"]
    key = answer_all(client, aid)
    first, second = list(key)[:2]
    assert client.put(f"{A}/{aid}/answers", json={"answers": {first: key[first]}, "times": {first: 12}}, headers=H()).json()["answered_questions"] == 1
    assert client.put(f"{A}/{aid}/answers", json={"answers": {second: key[second]}}, headers=H()).json() == {"saved": 1, "answered_questions": 2, "total_questions": 5}
    got = client.get(f"{A}/{aid}", headers=H()).json()                              # "browser refresh" / another device
    assert got["answers"] == {first: key[first], second: key[second]} and got["answered_questions"] == 2
    client.put(f"{A}/{aid}/answers", json={"answers": {first: 0}}, headers=H())      # changing an answer overwrites, never duplicates
    assert client.get(f"{A}/{aid}", headers=H()).json()["answers"][first] == 0
    client.put(f"{A}/{aid}/answers", json={"clear": [first]}, headers=H())
    assert first not in client.get(f"{A}/{aid}", headers=H()).json()["answers"]


def test_answer_validation_and_ownership(client, llm):
    aid = start(client, n=3).json()["assessment_id"]
    qid = next(iter(key_of(aid)))
    assert client.put(f"{A}/{aid}/answers", json={"answers": {"not-in-attempt": 1}}, headers=H()).status_code == 409
    assert client.put(f"{A}/{aid}/answers", json={"answers": {qid: {"x": 1}}}, headers=H()).status_code == 422
    assert client.put(f"{A}/{aid}/answers", json={"answers": {qid: True}}, headers=H()).status_code == 422
    assert client.put(f"{A}/{aid}/answers", json={"answers": {qid: 1}}, headers=H("intruder")).status_code == 404
    assert client.get(f"{A}/{aid}", headers=H("intruder")).status_code == 404
    client.post(f"{A}/{aid}/submit", json={"answers": {}}, headers=H())
    assert client.put(f"{A}/{aid}/answers", json={"answers": {qid: 1}}, headers=H()).status_code == 409   # closed after submission


def test_submit_scores_from_saved_answers_and_reports_everything_needed_for_review(client, llm):
    aid = start(client, n=10).json()["assessment_id"]
    ans = answer_all(client, aid, wrong=3)
    client.put(f"{A}/{aid}/answers", json={"answers": ans}, headers=H())
    r = client.post(f"{A}/{aid}/submit", json={"answers": {}}, headers=H())          # body empty: autosaved answers are graded
    d = r.json()
    assert r.status_code == 200 and d["score_pct"] == 70.0 and d["correct_count"] == 7 and d["incorrect_count"] == 3 and d["total_questions"] == 10
    assert d["status"] == "completed" and d["proficiency_level"] == "good" and d["already_submitted"] is False and d["time_taken_seconds"] >= 0
    assert len(d["review"]) == 10 and all("explanation" in x and "correct_options" in x and "selected" in x for x in d["review"])
    assert d["proficiency"]["proficiency_version"] == "prof-v1" and sum(c["questions_attempted"] for c in d["concepts"]) == 10
    row = get_container().assessments.attempt_row("learner-1", aid)
    assert row["status"] == "completed" and row["score"] == 70.0 and row["correct_answers"] == 7 and row["answered_questions"] == 10 and row["submitted_at"] is not None
    assert llm.calls == 0                                                            # scoring and analysis never touch a model


def test_answers_sent_with_submit_override_saved_ones_and_unanswered_score_zero(client, llm):
    aid = start(client, n=4).json()["assessment_id"]
    ans = answer_all(client, aid)
    first = list(ans)[0]
    client.put(f"{A}/{aid}/answers", json={"answers": {first: 0 if ans[first] != 0 else 1}}, headers=H())    # saved a wrong answer
    d = client.post(f"{A}/{aid}/submit", json={"answers": {k: v for k, v in list(ans.items())[:2]}}, headers=H()).json()
    assert d["correct_count"] == 2 and d["incorrect_count"] == 2 and d["score_pct"] == 50.0


def test_duplicate_submission_is_idempotent_and_creates_no_duplicate_results(client, llm):
    aid = start(client, n=6).json()["assessment_id"]
    ans = answer_all(client, aid, wrong=2)
    with ThreadPoolExecutor(5) as ex:
        res = list(ex.map(lambda _: client.post(f"{A}/{aid}/submit", json={"answers": ans}, headers=H()), range(5)))
    assert all(r.status_code == 200 for r in res) and sorted(r.json()["already_submitted"] for r in res).count(False) == 1
    assert len({r.json()["score_pct"] for r in res}) == 1
    c = get_container()
    assert len(c.assessments.concept_results(aid)) == len({s["concept"] for s in key_of(aid).values()})     # one row per concept, not five
    assert client.get("/api/v1/proficiency", headers=H()).json()["items"][0]["evidence_count"] >= 1
    ev = client.get("/api/v1/proficiency", headers=H()).json()["items"][0]["evidence_count"]
    client.post(f"{A}/{aid}/submit", json={"answers": ans}, headers=H())
    assert client.get("/api/v1/proficiency", headers=H()).json()["items"][0]["evidence_count"] == ev


def test_empty_submission_scores_zero_and_leaves_proficiency_untouched(client, llm):
    aid = start(client, "SQL", 3).json()["assessment_id"]
    d = client.post(f"{A}/{aid}/submit", json={"answers": {}}, headers=H()).json()
    assert d["score_pct"] == 0.0 and d["correct_count"] == 0 and client.get("/api/v1/proficiency", headers=H()).json()["items"] == []


def test_history_lists_resume_and_review_states_without_any_model_call(client, llm):
    done = start(client, n=3, hdr={"X-Idempotency-Key": "a"}).json()["assessment_id"]
    client.post(f"{A}/{done}/submit", json={"answers": answer_all(client, done)}, headers=H())
    open_ = start(client, "SQL", 3, hdr={"X-Idempotency-Key": "b"}).json()["assessment_id"]
    h = client.get(A, headers=H()).json()
    by = {x["assessment_id"]: x for x in h["items"]}
    assert h["total"] == 2 and by[done]["status"] == "completed" and by[done]["score"] == 1.0 and by[open_]["status"] == "open" and by[open_]["score"] is None
    assert by[done]["skill"] == "Python" and by[open_]["n_questions"] == 3
    assert client.get(f"{A}/{done}/results", headers=H()).status_code == 200 and client.get(f"{A}/{open_}/results", headers=H()).status_code == 409
    assert llm.calls == 0


def test_completed_attempts_stay_reviewable_after_the_bank_changes(client, llm):
    aid = start(client, n=5).json()["assessment_id"]
    client.post(f"{A}/{aid}/submit", json={"answers": answer_all(client, aid, wrong=1)}, headers=H())
    before = client.get(f"{A}/{aid}/results", headers=H()).json()["review"]
    store = get_container().assessments
    from sqlalchemy import update
    from app.infrastructure.database import tables as T
    with store.db.begin() as c:
        c.execute(update(T.assessment_question_bank).values(question_text="REWRITTEN", explanation="REWRITTEN", is_active=False, validation_status="deprecated"))
    after = client.get(f"{A}/{aid}/results", headers=H()).json()["review"]
    assert after == before and all("REWRITTEN" not in x["prompt"] for x in after)


# ------------------------------------------------------------------------------------------------ skill gaps
def python_attempt_with_weak_concept(client, n=10, wrong=None):
    """Fail every question of the attempt's largest concept area (deterministic, >=2 questions) and answer the rest correctly."""
    from collections import Counter
    aid = start(client, n=n).json()["assessment_id"]
    key = key_of(aid)
    weak = Counter(s["concept"] for s in key.values()).most_common(1)[0][0]
    ans = {}
    for qid, s in key.items():
        bad = [j for j in range(len(s["options"])) if j not in s["correct"]][0]
        ans[qid] = (bad if len(s["correct"]) == 1 else [bad]) if s["concept"] == weak else (s["correct"][0] if len(s["correct"]) == 1 else s["correct"])
    client.post(f"{A}/{aid}/submit", json={"answers": ans}, headers=H())
    return aid


def test_skill_gap_report_is_deterministic_structured_and_consistent_with_the_answers(client, llm):
    aid = python_attempt_with_weak_concept(client, 10)
    g = client.get(f"{A}/{aid}/skill-gaps", headers=H())
    d = g.json()
    assert g.status_code == 200 and d["skill"] == "Python" and d["overall_score"] == client.get(f"{A}/{aid}/results", headers=H()).json()["score_pct"] and d["questions"] == 10
    assert len(d["critical_gaps"]) == 1 and d["strong_concepts"]
    for k in ("strong_concepts", "weak_concepts", "critical_gaps", "unknown_concepts", "recommended_sections", "recommended_learning_order", "prerequisite_gaps", "by_difficulty",
              "by_question_type", "by_cognitive_level", "concepts", "caveat", "basis"):
        assert k in d
    got = {r["concept"]: r for r in client.get(f"{A}/{aid}/results", headers=H()).json()["concepts"]}
    review = client.get(f"{A}/{aid}/results", headers=H()).json()["review"]
    for concept, row in got.items():                                                # recompute accuracy independently from the review rows
        rows = [x for x in review if x["concept"] == concept]
        assert row["accuracy"] == pytest.approx(sum(x["is_correct"] for x in rows) / len(rows), abs=1e-3) and row["questions_attempted"] == len(rows)
    assert set(d["critical_gaps"]) | set(d["weak_concepts"]) == {c for c, r in got.items() if r["proficiency_level"] == "needs_improvement"}
    assert not set(d["strong_concepts"]) & set(d["critical_gaps"] + d["weak_concepts"])
    assert [s["concept"] for s in d["recommended_sections"]] == d["recommended_learning_order"]
    assert llm.calls == 0 and "no AI model" in d["basis"]


def test_concepts_with_too_few_questions_are_unknown_not_weak(client, llm):
    aid = start(client, n=3).json()["assessment_id"]                                 # 3 questions across >=3 concepts: each concept has 1 question
    client.post(f"{A}/{aid}/submit", json={"answers": {}}, headers=H())
    d = client.get(f"{A}/{aid}/skill-gaps", headers=H()).json()
    assert d["critical_gaps"] == d["weak_concepts"] == d["recommended_learning_order"] == [] and len(d["unknown_concepts"]) == 3
    assert "short assessment" in d["caveat"]


def test_gap_endpoints_require_a_submitted_attempt_and_ownership(client, llm):
    aid = start(client, n=3).json()["assessment_id"]
    assert client.get(f"{A}/{aid}/skill-gaps", headers=H()).status_code == 409
    assert client.post(f"{A}/{aid}/generate-learning-plan", headers=H()).status_code == 409
    client.post(f"{A}/{aid}/submit", json={"answers": {}}, headers=H())
    for method, path in (("get", f"{A}/{aid}/skill-gaps"), ("get", f"{A}/{aid}/results"), ("post", f"{A}/{aid}/generate-learning-plan"), ("post", f"{A}/{aid}/submit")):
        assert getattr(client, method)(path, headers=H("intruder"), **({"json": {"answers": {}}} if path.endswith("submit") else {})).status_code == 404, path


# ------------------------------------------------------------------------------------------------ learning plan
class Recorder:
    def __init__(self):
        self.prompts = []

    async def __call__(self, sec, prompt):
        from tests.unit.test_content_generation import good_output
        self.prompts.append(prompt)
        return good_output(sec), False


def test_learning_plan_targets_only_identified_gaps_and_links_back_to_the_assessment(client, monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(cg, "_call_model", rec)
    get_container().bank.call = NoLLM()
    aid = python_attempt_with_weak_concept(client, 10)
    gaps = client.get(f"{A}/{aid}/skill-gaps", headers=H()).json()
    assert gaps["recommended_learning_order"], "fixture must produce at least one gap"
    r = client.post(f"{A}/{aid}/generate-learning-plan", headers=H())
    assert r.status_code == 201 and r.json()["created"] is True
    sid = r.json()["session_id"]
    s = client.get(f"/api/v1/content-sessions/{sid}", headers=H()).json()
    m = s["metadata"]
    assert m["source_assessment_id"] == aid and m["target_skill_id"] == gaps["skill_id"] and m["identified_skill_gaps"] == gaps["recommended_learning_order"]
    assert m["recommended_concepts"] == gaps["recommended_learning_order"] and len(m["learning_objectives"]) == len(gaps["recommended_learning_order"])
    keys = [x["section_key"] for x in s["sections"]]
    from app.domains.assessments.analysis import slug
    assert keys[0] == "plan-overview" and keys[-1] == "plan-practice" and {k for k in keys if k.startswith("gap-")} == {f"gap-{slug(c)}" for c in gaps["recommended_learning_order"]}
    assert s["status"] == "active" and s["completed_sections"] == 0 and s["content_type"] == "course" and rec.prompts == []      # creating the plan generates nothing yet
    assert client.get(f"{A}/{aid}", headers=H()).json()["learning_session_id"] == sid

    ev = sse(client.post(f"/api/v1/content-sessions/{sid}/continue", headers=H()).text)        # generation runs through the normal persistent-session pipeline
    assert ev[-1][0] == "done" and ev[-1][1]["incomplete_sections"] == []
    done = client.get(f"/api/v1/content-sessions/{sid}", headers=H()).json()
    assert done["status"] == "completed" and done["completed_sections"] == len(keys)
    gap_prompts = [p for p in rec.prompts if "SECTION: concept" in p]
    assert len(gap_prompts) == len(gaps["recommended_learning_order"])
    for concept in gaps["recommended_learning_order"]:
        assert any(f"'{concept}'" in p for p in gap_prompts)
    strong = gaps["strong_concepts"]
    assert all(not any(f"concept '{c}'" in p for c in strong) for p in gap_prompts)             # strong concepts are not re-taught
    assert all("mermaid" in p.lower() and "KaTeX" in p and "knowledge-check" in p for p in gap_prompts)
    n = len(rec.prompts)
    sse(client.post(f"/api/v1/content-sessions/{sid}/continue", headers=H()).text)             # reopening/continuing a finished plan costs nothing
    assert len(rec.prompts) == n


def test_generate_plan_is_idempotent_and_survives_double_click(client, llm):
    aid = python_attempt_with_weak_concept(client, 10)
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda _: client.post(f"{A}/{aid}/generate-learning-plan", headers=H()), range(4)))
    assert len({r.json()["session_id"] for r in res}) == 1 and sum(r.json()["created"] for r in res) == 1
    sessions = client.get("/api/v1/content-sessions", headers=H()).json()
    assert sessions["total"] == 1


def test_a_perfect_attempt_has_nothing_to_target(client, llm):
    aid = start(client, n=10).json()["assessment_id"]
    client.post(f"{A}/{aid}/submit", json={"answers": answer_all(client, aid)}, headers=H())
    r = client.post(f"{A}/{aid}/generate-learning-plan", headers=H())
    assert r.status_code == 409 and "no skill gaps" in r.json()["error"]["message"].lower()
    assert client.get("/api/v1/content-sessions", headers=H()).json()["total"] == 0


def test_plan_session_appears_in_content_history_search_by_gap_concept(client, llm):
    aid = python_attempt_with_weak_concept(client, 10)
    gaps = client.get(f"{A}/{aid}/skill-gaps", headers=H()).json()["recommended_learning_order"]
    client.post(f"{A}/{aid}/generate-learning-plan", headers=H())
    hit = client.get("/api/v1/content-sessions/search", params={"q": gaps[0].split()[0]}, headers=H()).json()
    assert hit["total"] == 1 and hit["items"][0]["content_type"] == "course"


# ------------------------------------------------------------------------------------------------ bank metadata endpoints
def test_bank_skill_listing_availability_and_topics(client, llm):
    d = client.get("/api/v1/assessment-question-bank/skills", headers=H()).json()
    by = {s["name"]: s for s in d["items"]}
    assert by["Python"]["question_count"] == 18 and by["Python"]["concepts"] == 5 and by["JavaScript"]["question_count"] == 0 and d["total"] == len(d["items"]) >= 33
    assert sum(1 for s in d["items"] if s["recommended"]) == 4 and all(s["question_count"] >= 10 for s in d["items"] if s["recommended"])
    assert {s["name"] for s in client.get("/api/v1/assessment-question-bank/skills", params={"q": "postgres"}, headers=H()).json()["items"]} == {"PostgreSQL"}
    av = client.get(f"/api/v1/assessment-question-bank/skills/{by['Python']['skill_id']}/availability", headers=H()).json()
    assert av["total"] == 18 and av["ready_for"] == [3, 5, 10, 15] and set(av["by_difficulty"]) == {"1", "2", "3", "4"} and int(av["estimated_minutes"]["10"]) >= 1
    tp = client.get(f"/api/v1/assessment-question-bank/skills/{by['Python']['skill_id']}/topics", headers=H()).json()["items"]
    assert len(tp) == 5 and all({"concept", "questions", "min_difficulty", "max_difficulty"} <= set(t) for t in tp)
    assert client.get("/api/v1/assessment-question-bank/skills/nope/availability", headers=H()).status_code == 404
    assert client.get("/api/v1/assessment-question-bank/skills").status_code in (401, 403)
    assert llm.calls == 0


def test_proficiency_shows_up_as_previous_proficiency_in_the_skill_listing(client, llm):
    aid = start(client, n=5).json()["assessment_id"]
    client.post(f"{A}/{aid}/submit", json={"answers": answer_all(client, aid)}, headers=H())
    py = next(s for s in client.get("/api/v1/assessment-question-bank/skills", headers=H()).json()["items"] if s["name"] == "Python")
    assert py["assessed"] is True and py["proficiency"] is not None


# ------------------------------------------------------------------------------------------------ legacy attempts
def test_attempts_created_before_the_bank_existed_still_open_and_submit(client, llm):
    from datetime import datetime, timezone
    from app.infrastructure.database.learner_store import Attempt
    c = get_container()
    skill = c.registry.resolve("Python", 1.0).skill_id
    c.store.save_attempt(Attempt("legacy-1", "learner-1", skill, ["py-1", "py-2"], datetime.now(timezone.utc)))
    g = client.get(f"{A}/legacy-1", headers=H()).json()
    assert g["status"] == "open" and [q["question_id"] for q in g["questions"]] == ["py-1", "py-2"]
    d = client.post(f"{A}/legacy-1/submit", json={"answers": {"py-1": 1, "py-2": 2}}, headers=H()).json()
    assert d["score_pct"] == 100.0 and d["correct_count"] == 2


def test_an_assessment_in_progress_survives_an_api_restart_and_a_second_device(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.core.config import get_settings
    from app.core.container import reset_container
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "app.db"))
    get_settings.cache_clear(); reset_container()
    import main
    try:
        with TestClient(main.app) as c1:
            a = c1.post(A, json={"skill": "Python", "n_questions": 5}, headers=H(**{"X-Idempotency-Key": "restart-1"})).json()
            key = key_of(a["assessment_id"]); first = next(iter(key))
            assert c1.put(f"{A}/{a['assessment_id']}/answers", json={"answers": {first: 1}}, headers=H()).status_code == 200
        reset_container()                                                              # the API process "restarts": every in-memory structure is gone
        with TestClient(main.app) as c2:                                               # another device, same account
            g = c2.get(f"{A}/{a['assessment_id']}", headers=H()).json()
            assert g["status"] == "open" and g["answers"] == {first: 1} and [q["question_id"] for q in g["questions"]] == [q["question_id"] for q in a["questions"]]
            assert c2.post(A, json={"skill": "Python", "n_questions": 5}, headers=H(**{"X-Idempotency-Key": "restart-1"})).json()["assessment_id"] == a["assessment_id"]
            done = c2.post(f"{A}/{a['assessment_id']}/submit", json={"answers": {}}, headers=H()).json()
            assert done["status"] == "completed" and done["total_questions"] == 5
        reset_container()
        with TestClient(main.app) as c3:                                               # and a completed attempt is still reviewable after yet another restart
            r = c3.get(f"{A}/{a['assessment_id']}/results", headers=H()).json()
            assert r["score_pct"] == done["score_pct"] and len(r["review"]) == 5 and c3.get(A, headers=H()).json()["total"] == 1
    finally:
        reset_container(); get_settings.cache_clear()
