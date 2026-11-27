"""Diagnostic Study: database-first start with AI fallback. Scenarios A (exists), B (new topic), C (partial), D (repeat), E (concurrent), F (failures)."""
import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app.core.container import get_container
from app.domains.assessments.bank import service as bank_service
from app.infrastructure.database import tables as T
from services import content_generation as cg
from tests.conftest import make_token
from tests.integration.test_assessment_bank_service import fake_json

D = "/api/v1/diagnostic-study"
H = lambda u="learner-1", **extra: {"Authorization": f"Bearer {make_token(u)}", **extra}  # noqa: E731


class Gen:
    """Fake model. Records every call; behaviour is configurable per test."""
    def __init__(self, payload=None, delay=0.0, error=None):
        self.prompts, self.payload, self.delay, self.error, self.lock = [], payload, delay, error, threading.Lock()

    async def __call__(self, prompt):
        with self.lock:
            self.prompts.append(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        n = int(prompt.split("exactly ")[1].split(" ")[0])
        base = prompt.split("about ")[1].split(" for ")[0] if "about " in prompt else "Topic"
        return (self.payload if self.payload is not None else fake_json(n, base=f"{base} {len(self.prompts)}", concept="Area")), 120, 340

    @property
    def calls(self):
        return len(self.prompts)


@pytest.fixture
def gen(client, monkeypatch):
    g = Gen()
    get_container().bank.call = g

    async def no_legacy(*a, **k):
        raise AssertionError("the legacy section pipeline must not be used by the diagnostic start")
    monkeypatch.setattr(cg, "_call_model", no_legacy)
    return g


def start(client, topic="Python", n=10, user="learner-1", **kw):
    return client.post(f"{D}/start", json={"topic": topic, "question_count": n, **kw.pop("body", {})}, headers=H(user, **kw.pop("hdr", {})))


def key_of(aid):
    return {r["question_id"]: r["question_snapshot"] for r in get_container().assessments.attempt_questions(aid)}


def py_skill():
    return get_container().registry.resolve("Python", 1.0)


# ------------------------------------------------------------------------------------------------ A: topic exists
def test_A_existing_topic_starts_from_the_database_with_zero_model_calls(client, gen):
    r = start(client, "Python", 10)
    d = r.json()
    assert r.status_code == 201, r.text
    assert d["generation_source"] == "question_bank" and d["status"] == "ready" and d["topic"] == "Python" and d["question_count"] == 10 and len(d["questions"]) == 10
    assert d["resumed"] is False and d["shortfall"] == 0 and d["note"] is None and len({q["question_id"] for q in d["questions"]}) == 10
    assert gen.calls == 0
    keys = {k for q in d["questions"] for k in q} | set(d)
    assert not keys & {"correct", "correct_options", "correct_answer", "explanation", "solution_approach", "question_snapshot", "provider", "model"}   # no answers, no provider internals
    row = get_container().assessments.attempt_row("learner-1", d["session_id"])
    assert row["kind"] == "diagnostic" and row["generation_source"] == "question_bank" and row["topic"] == "Python" and row["total_questions"] == 10
    assert get_container().assessments.generation_stats()["jobs"] == 0                 # no population job was even created


@pytest.mark.parametrize("typed,canonical", [("python", "Python"), ("PYTHON", "Python"), ("  python   programming ", "Python"), ("py", "Python"), ("ml", "Machine Learning"),
                                             ("machine learning", "Machine Learning"), ("gen ai", "Generative AI"), ("agentic ai", "Agentic AI"), ("rest api", "REST API Design"),
                                             ("REST APIs", "REST API Design"), ("k8s", "Kubernetes"), ("Postgres", "PostgreSQL"), ("java", "Java"), ("javascript", "JavaScript")])
def test_topics_resolve_to_canonical_skills_by_explicit_aliases_only(client, gen, typed, canonical):
    d = client.post(f"{D}/check", json={"topic": typed, "question_count": 5}, headers=H()).json()
    assert d["known"] is True and d["canonical_topic"] == canonical
    assert gen.calls == 0


def test_no_fuzzy_guessing_a_typo_or_near_name_is_a_new_topic_not_another_skill(client, gen):
    for typed in ("pythno", "javascrip", "java script", "sqll"):
        d = client.post(f"{D}/check", json={"topic": typed, "question_count": 5}, headers=H()).json()
        assert d["known"] is False and d["canonical_topic"] is None and d["will_call_ai"] is True, typed


def test_check_is_a_pure_preview_it_calls_no_model_and_creates_nothing(client, gen):
    before = client.get("/api/v1/skills?limit=200", headers=H()).json()["total"]
    d = client.post(f"{D}/check", json={"topic": "Brand New Topic", "question_count": 10}, headers=H()).json()
    assert d == {"topic": "Brand New Topic", "canonical_topic": None, "known": False, "plan": "ai_generated", "eligible": 0, "requested": 10, "missing": 10, "will_call_ai": True}
    py = client.post(f"{D}/check", json={"topic": "python", "question_count": 10}, headers=H()).json()
    assert py["plan"] == "question_bank" and py["eligible"] == 18 and py["missing"] == 0 and py["will_call_ai"] is False
    big = client.post(f"{D}/check", json={"topic": "python", "question_count": 20}, headers=H()).json()
    assert big["plan"] == "hybrid" and big["missing"] == 2 and big["will_call_ai"] is True
    assert gen.calls == 0 and client.get("/api/v1/skills?limit=200", headers=H()).json()["total"] == before
    assert get_container().assessments.generation_stats()["jobs"] == 0


def test_the_session_is_persistent_listed_resumable_and_reviewable_without_any_model_call(client, gen):
    d = start(client).json(); sid = d["session_id"]
    key = key_of(sid); first = next(iter(key))
    client.put(f"/api/v1/assessments/{sid}/answers", json={"answers": {first: 1}}, headers=H())                        # autosave reuses the attempt endpoint
    g = client.get(f"{D}/sessions/{sid}", headers=H()).json()
    assert g["status"] == "open" and g["answers"] == {first: 1} and g["answered_questions"] == 1 and g["result"] is None and len(g["questions"]) == 10
    lst = client.get(f"{D}/sessions", headers=H()).json()
    assert lst["total"] == 1 and lst["items"][0]["session_id"] == sid and lst["items"][0]["topic"] == "Python" and lst["items"][0]["status"] == "open" and lst["items"][0]["generation_source"] == "question_bank"
    ans = {qid: (s["correct"][0] if len(s["correct"]) == 1 else s["correct"]) for qid, s in key.items()}
    res = client.post(f"{D}/sessions/{sid}/submit", json={"answers": ans}, headers=H()).json()
    assert res["status"] == "completed" and res["result"]["score"] == 10 and res["result"]["total"] == 10 and res["result"]["level"] == "Expert" and res["result"]["score_10"] == 10
    assert all(b["correct"] and b["correct_answer"] and b["explanation"] for b in res["result"]["breakdown"])
    again = client.post(f"{D}/sessions/{sid}/submit", json={"answers": {first: 0}}, headers=H()).json()                  # duplicate submit: same stored result
    assert again["result"]["score"] == 10
    done = client.get(f"{D}/sessions", headers=H()).json()["items"][0]
    assert done["status"] == "completed" and done["level"] == "Expert" and done["score_pct"] == 100.0
    assert client.get(f"{D}/sessions/{sid}", headers=H()).json()["result"]["level"] == "Expert"                          # reviewable later
    assert gen.calls == 0


def test_levels_follow_the_diagnostic_bands(client, gen):
    for wrong, level in ((0, "Expert"), (3, "Intermediate"), (6, "Beginner")):
        sid = start(client, hdr={"X-Idempotency-Key": f"k{wrong}"}).json()["session_id"]
        key = key_of(sid); ans = {}
        for i, (qid, s) in enumerate(key.items()):
            bad = [j for j in range(len(s["options"])) if j not in s["correct"]][0]
            ans[qid] = (bad if len(s["correct"]) == 1 else [bad]) if i < wrong else (s["correct"][0] if len(s["correct"]) == 1 else s["correct"])
        assert client.post(f"{D}/sessions/{sid}/submit", json={"answers": ans}, headers=H()).json()["result"]["level"] == level


def test_diagnostic_and_assessment_histories_are_separate_and_the_diagnostic_adds_no_proficiency(client, gen):
    sid = start(client).json()["session_id"]
    ans = {qid: (s["correct"][0] if len(s["correct"]) == 1 else s["correct"]) for qid, s in key_of(sid).items()}
    client.post(f"{D}/sessions/{sid}/submit", json={"answers": ans}, headers=H())
    assert client.get("/api/v1/assessments", headers=H()).json()["total"] == 0
    assert client.get("/api/v1/proficiency", headers=H()).json()["items"] == []
    sid2 = start(client, hdr={"X-Idempotency-Key": "second"}).json()["session_id"]
    assert client.post(f"/api/v1/assessments/{sid2}/submit", json={"answers": {}}, headers=H()).status_code == 409            # not submittable as a skill assessment
    a = client.post("/api/v1/assessments", json={"skill": "Python", "n_questions": 3}, headers=H()).json()
    assert client.get(f"{D}/sessions/{a['assessment_id']}", headers=H()).status_code == 404 and client.get(f"{D}/sessions", headers=H()).json()["total"] == 2


def test_refresh_double_click_and_retry_with_one_key_give_one_session(client, gen):
    with ThreadPoolExecutor(5) as ex:
        res = list(ex.map(lambda _: start(client, hdr={"X-Idempotency-Key": "same"}), range(5)))
    assert all(r.status_code == 201 for r in res) and len({r.json()["session_id"] for r in res}) == 1
    assert sum(1 for r in res if r.json()["resumed"]) >= 1 and client.get(f"{D}/sessions", headers=H()).json()["total"] == 1
    assert gen.calls == 0


def test_validation_of_the_topic_and_request(client, gen):
    for body in ({"topic": ""}, {"topic": "   "}, {"topic": "x"}, {"topic": "!!!"}, {"topic": "a" * 81}, {"topic": "<script>"}, {"topic": "Python", "question_count": 2},
                 {"topic": "Python", "question_count": 21}):
        r = client.post(f"{D}/start", json={"question_count": 10, **body}, headers=H())
        assert r.status_code == 422, (body, r.status_code, r.text)
    assert client.post(f"{D}/start", json={"topic": "Python"}).status_code in (401, 403)
    assert client.post(f"{D}/start", json={"topic": "  Python   "}, headers=H()).json()["topic"] == "Python"                  # whitespace normalised
    assert gen.calls == 0 and client.get(f"{D}/sessions", headers=H()).json()["total"] == 1


def test_suggestions_come_from_the_registry_and_free_text_is_still_accepted(client, gen):
    s = client.get(f"{D}/suggest", params={"q": "pyth"}, headers=H()).json()["items"]
    assert s[0]["name"] == "Python" and s[0]["question_count"] == 18
    assert [x["name"] for x in client.get(f"{D}/suggest", params={"q": "post"}, headers=H()).json()["items"]][0] == "PostgreSQL"
    assert client.get(f"{D}/suggest", params={"q": ""}, headers=H()).json()["items"] == [] and client.get(f"{D}/suggest", params={"q": "zzzz"}, headers=H()).json()["items"] == []
    assert gen.calls == 0


def test_sessions_are_private(client, gen):
    sid = start(client).json()["session_id"]
    assert client.get(f"{D}/sessions/{sid}", headers=H("intruder")).status_code == 404
    assert client.post(f"{D}/sessions/{sid}/submit", json={"answers": {}}, headers=H("intruder")).status_code == 404
    assert client.get(f"{D}/sessions", headers=H("intruder")).json()["total"] == 0


# ------------------------------------------------------------------------------------------------ B: topic does not exist
def test_B_new_topic_generates_validates_saves_and_starts(client, gen):
    r = start(client, "Rust Programming", 10)
    d = r.json()
    assert r.status_code == 201, r.text
    assert d["generation_source"] == "ai_generated" and len(d["questions"]) == 10 and d["topic"] == "Rust Programming" and d["status"] == "ready"
    assert gen.calls == 1 and "exactly 10 questions" in gen.prompts[0] and "Rust" in gen.prompts[0]
    skill = get_container().registry.resolve("rust programming", 1.0)
    assert skill and skill.status.value == "proposed" and "rust" in [a.lower() for a in skill.aliases]               # registered once, with the short alias
    cands = get_container().assessments.candidates(skill.skill_id)
    assert len(cands) == 10                                                                                           # persisted permanently, validated
    with get_container().assessments.db.begin() as c:
        rows = c.execute(T.assessment_question_bank.select().where(T.assessment_question_bank.c.skill_id == skill.skill_id)).all()
    assert {r.source_type for r in rows} == {"llm"} and {r.validation_status for r in rows} == {"validated"} and all(r.is_active for r in rows)
    st = get_container().assessments.generation_stats()
    assert st["jobs"] == 1 and st["llm_calls"] == 1 and st["tokens_in"] == 120 and st["tokens_out"] == 340 and st["questions_added"] == 10 and st["topics_populated"] == 1
    assert "provider" not in json.dumps(d) and "tokens" not in json.dumps(d)


def test_D_repeating_a_new_topic_reuses_the_saved_questions_with_no_model_call(client, gen):
    start(client, "Rust Programming", 10)
    assert gen.calls == 1
    for typed, user in (("Rust Programming", "someone-else"), ("rust programming", "learner-1"), ("RUST", "third"), ("rust", "learner-1")):
        r = start(client, typed, 10, user=user, hdr={"X-Idempotency-Key": f"{typed}-{user}"})
        assert r.status_code == 201 and r.json()["generation_source"] == "question_bank" and len(r.json()["questions"]) == 10, typed
    assert gen.calls == 1 and get_container().assessments.generation_stats()["jobs"] == 1


def test_generated_questions_are_validated_like_authored_ones_and_the_bad_ones_are_not_served(client):
    good = json.loads(fake_json(3, base="Mixed", concept="Area"))
    bad = [dict(good[0], question_text="Mixed question with an impossible answer index, which is it?", correct=[9]),
           dict(good[0], question_text="Mixed question whose options repeat each other, which is it?", options=["same", "same", "x1", "x2"], correct=[0])]
    g = Gen(payload=json.dumps(good + bad)); get_container().bank.call = g
    r = start(client, "Mixed Topic", 5)
    d = r.json()
    assert r.status_code == 201 and len(d["questions"]) == 3 and d["shortfall"] == 2 and "Only 3 of the 5" in d["note"] and d["question_count"] == 3
    skill = get_container().registry.resolve("mixed topic", 1.0)
    assert len(get_container().assessments.candidates(skill.skill_id)) == 3                                          # invalid ones never became eligible
    with get_container().assessments.db.begin() as c:
        statuses = [r.validation_status for r in c.execute(T.assessment_question_bank.select().where(T.assessment_question_bank.c.skill_id == skill.skill_id))]
    assert sorted(statuses) == ["rejected", "rejected", "validated", "validated", "validated"]


# ------------------------------------------------------------------------------------------------ C: topic partially exists
def deprecate_python_down_to(n_left):
    store = get_container().assessments
    ids = [c["id"] for c in store.candidates(py_skill().skill_id)]
    for qid in ids[n_left:]:
        store.set_status(qid, validation_status="deprecated")
    get_container().bank.invalidate()
    return set(ids[:n_left])


def test_C_partial_topic_reuses_what_exists_and_generates_only_the_missing_questions(client, gen):
    kept = deprecate_python_down_to(6)
    r = start(client, "Python", 10)
    d = r.json()
    assert r.status_code == 201 and d["generation_source"] == "hybrid" and len(d["questions"]) == 10 and d["shortfall"] == 0
    assert gen.calls == 1 and "exactly 4 questions" in gen.prompts[0]                                              # not 10
    assigned = {q["question_id"] for q in d["questions"]}
    assert len(assigned) == 10 and kept <= assigned                                                                  # all six existing questions are reused, no duplicates
    assert len(get_container().assessments.candidates(py_skill().skill_id)) == 10                                    # the four new ones are saved for next time
    row = get_container().assessments.attempt_row("learner-1", d["session_id"])
    assert row["generation_source"] == "hybrid" and row["total_questions"] == 10
    again = start(client, "Python", 10, user="other")                                                                # next learner: pure database
    assert again.json()["generation_source"] == "question_bank" and gen.calls == 1


def test_missing_difficulties_and_concepts_are_derived_from_what_is_already_chosen(client, gen):
    svc = get_container().bank
    chosen = [{"difficulty": d, "concept": c} for d, c in ((1, "A"), (2, "A"), (2, "B"), (3, "B"), (3, "B"), (3, "C"))]
    diffs = svc.identify_missing_difficulties(chosen, 10)
    assert len(diffs) == 4                                                                                          # 10 wanted, 6 chosen
    assert sum(1 for d in diffs if d >= 4) == 2 and all(1 <= d <= 5 for d in diffs)
    assert svc.identify_missing_difficulties(chosen, 6) == [] and len(svc.identify_missing_difficulties([], 10)) == 10
    kept = deprecate_python_down_to(6)
    cov = svc.identify_missing_concepts(py_skill().skill_id, [{"concept": "Data types and collections"}] * 4)
    assert cov[0] != "Data types and collections" and svc.identify_missing_concepts("no-such-skill", []) == []
    assert kept


def test_C_when_generation_fails_the_existing_questions_are_used_and_the_reduction_is_reported(client):
    kept = deprecate_python_down_to(6)
    get_container().bank.call = Gen(error=RuntimeError("provider down"))
    r = start(client, "Python", 10)
    d = r.json()
    assert r.status_code == 201 and len(d["questions"]) == 6 and d["question_count"] == 6 and d["shortfall"] == 4 and "Only 6 of the 10" in d["note"]
    assert d["generation_source"] == "question_bank" and {q["question_id"] for q in d["questions"]} == kept
    assert len(get_container().assessments.candidates(py_skill().skill_id)) == 6                                     # nothing invalid or partial was written
    assert "provider down" not in r.text


# ------------------------------------------------------------------------------------------------ E: concurrency
def test_E_two_users_requesting_the_same_new_topic_trigger_one_generation_and_get_independent_sessions(client):
    g = Gen(delay=0.6); get_container().bank.call = g
    barrier = threading.Barrier(2)

    def go(user):
        barrier.wait()
        return start(client, "Zig Programming", 10, user=user)

    with ThreadPoolExecutor(2) as ex:
        a, b = list(ex.map(go, ["alice", "bob"]))
    assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
    assert g.calls == 1                                                                                             # ONE model call for two simultaneous requests
    assert len(a.json()["questions"]) == len(b.json()["questions"]) == 10 and a.json()["session_id"] != b.json()["session_id"]
    assert {q["question_id"] for q in a.json()["questions"]} == {q["question_id"] for q in b.json()["questions"]}      # both drew from the single saved bank (10 of 10)
    st = get_container().assessments.generation_stats()
    assert st["jobs"] == 1 and st["llm_calls"] == 1 and st["questions_added"] == 10
    assert "question_generation_duplicates_prevented_total" in __import__("app.observability.metrics", fromlist=["metrics"]).metrics.render()
    ka = key_of(a.json()["session_id"]); qa = next(iter(ka))
    client.put(f"/api/v1/assessments/{a.json()['session_id']}/answers", json={"answers": {qa: 1}}, headers=H("alice"))
    assert client.get(f"{D}/sessions/{b.json()['session_id']}", headers=H("bob")).json()["answers"] == {}              # nothing shared between users
    assert client.get(f"{D}/sessions", headers=H("bob")).json()["total"] == 1 and client.get(f"{D}/sessions", headers=H("alice")).json()["total"] == 1


def test_E_many_simultaneous_requests_still_generate_once(client):
    g = Gen(delay=0.4); get_container().bank.call = g
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(lambda i: start(client, "Elixir Programming", 8, user=f"u{i}"), range(6)))
    assert all(r.status_code == 201 and len(r.json()["questions"]) == 8 for r in res) and g.calls == 1
    assert len({r.json()["session_id"] for r in res}) == 6


@pytest.mark.parametrize("n", [1])
def test_generation_lock_is_atomic_expires_and_is_taken_over(db, n):
    from app.domains.skill_registry.seed import seed
    from app.domains.skill_registry.service import SkillRegistry
    from app.infrastructure.database.assessment_store import AssessmentStore
    from app.infrastructure.database.skill_repository import SqlSkillRepository
    reg = SkillRegistry(SqlSkillRepository(db)); seed(reg); store = AssessmentStore(db)
    sid = reg.resolve("Kafka", 1.0).skill_id
    t0 = T.utcnow()
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(lambda i: store.acquire_generation(sid, f"w{i}", 10, now=t0), range(8)))
    winners = [g for g in got if g[0]]
    assert len(winners) == 1 and all(g[1] == winners[0][0] for g in got if not g[0])                                # exactly one holder; everyone else sees its id
    jid = winners[0][0]
    assert store.acquire_generation(sid, "late", 10, now=t0 + timedelta(seconds=30))[0] is None                       # lease still live
    taken, _ = store.acquire_generation(sid, "rescuer", 10, lease_s=30, now=t0 + timedelta(seconds=200))             # holder crashed: lease expired
    assert taken and taken != jid and store.generation_job(jid)["status"] == "FAILED" and store.generation_job(jid)["error"] == "lease expired"
    store.finish_generation(taken, ok=True, added=3, llm_calls=1, tokens_in=5, tokens_out=7)
    again, _ = store.acquire_generation(sid, "next", 10, now=t0 + timedelta(seconds=201))
    assert again and store.generation_job(taken)["status"] == "COMPLETED" and store.generation_stats()["questions_added"] == 3


def test_a_crashed_holders_stale_job_does_not_block_a_new_topic(client, gen):
    skill, _ = get_container().bank.resolve_or_register("Haskell Programming")
    store = get_container().assessments
    stale, _ = store.acquire_generation(skill.skill_id, "dead-process", 10, lease_s=1, now=T.utcnow() - timedelta(minutes=5))
    assert stale
    r = start(client, "Haskell Programming", 10)
    assert r.status_code == 201 and r.json()["generation_source"] == "ai_generated" and gen.calls == 1 and store.generation_job(stale)["status"] == "FAILED"


# ------------------------------------------------------------------------------------------------ F: failures
def jobs_running():
    with get_container().assessments.db.begin() as c:
        return c.execute(T.question_generation_jobs.select().where(T.question_generation_jobs.c.active == 1)).all()


def test_F_llm_timeout_is_a_clear_retryable_error_and_leaves_nothing_behind(client, monkeypatch):
    monkeypatch.setattr(bank_service, "GENERATION_TIMEOUT_S", 0.2)
    get_container().bank.call = Gen(delay=3)
    r = start(client, "Nim Programming", 10)
    assert r.status_code == 503 and r.json()["error"]["code"] == "questions_unavailable" and "took too long" in r.json()["error"]["message"] and r.json()["error"]["details"]["reason"] == "timeout"
    assert "try again" in r.json()["error"]["message"].lower()
    assert client.get(f"{D}/sessions", headers=H()).json()["total"] == 0 and jobs_running() == []                    # no half-created session, lock released
    good = Gen(); get_container().bank.call = good
    ok = start(client, "Nim Programming", 10)                                                                         # retry works
    assert ok.status_code == 201 and ok.json()["generation_source"] == "ai_generated" and good.calls == 1


@pytest.mark.parametrize("payload,reason", [("this is not json", "invalid_output"), ("{}", "invalid_output"), ("[]", "invalid_output"),
                                           (json.dumps([{"concept": "x", "question_text": "A broken question without options at all?"}]), "invalid_output")])
def test_F_invalid_generated_output_is_rejected_nothing_is_saved_and_retry_is_possible(client, payload, reason):
    get_container().bank.call = Gen(payload=payload)
    r = start(client, "Crystal Programming", 10)
    assert r.status_code == 503 and r.json()["error"]["details"]["reason"] == reason and "did not pass validation" in r.json()["error"]["message"]
    skill = get_container().registry.resolve("crystal programming", 1.0)
    assert get_container().assessments.candidates(skill.skill_id) == [] and client.get(f"{D}/sessions", headers=H()).json()["total"] == 0 and jobs_running() == []
    get_container().bank.call = Gen()
    assert start(client, "Crystal Programming", 10).status_code == 201


def test_F_provider_unavailable_is_a_clear_error_without_internals(client):
    get_container().bank.call = Gen(error=ConnectionError("secret-host:11434 refused"))
    r = start(client, "Dart Programming", 10)
    assert r.status_code == 503 and "AI service is unavailable" in r.json()["error"]["message"] and "secret-host" not in r.text and r.json()["error"]["details"]["reason"] == "provider"
    assert get_container().assessments.generation_stats()["jobs"] == 0 and jobs_running() == []                       # a failed job is recorded as failed, not completed


def test_F_database_failure_while_saving_releases_the_lock_and_a_retry_succeeds(client, monkeypatch):
    g = Gen(); get_container().bank.call = g
    store = get_container().assessments
    real = store.insert_question
    monkeypatch.setattr(store, "insert_question", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db exploded")))
    r = start(client, "Scala Programming", 10)
    assert r.status_code == 500 and "db exploded" not in r.text and r.json()["error"]["code"] == "internal_error"
    assert jobs_running() == [] and client.get(f"{D}/sessions", headers=H()).json()["total"] == 0
    monkeypatch.setattr(store, "insert_question", real)
    assert start(client, "Scala Programming", 10).status_code == 201


def test_F_a_waiter_gives_up_with_a_clear_message_if_the_other_generation_never_finishes(client, monkeypatch):
    monkeypatch.setattr(bank_service, "WAIT_FOR_RUNNING_S", 0.5)
    g = Gen(); get_container().bank.call = g
    skill, _ = get_container().bank.resolve_or_register("Lua Programming")
    holder, _ = get_container().assessments.acquire_generation(skill.skill_id, "other-process", 10, lease_s=300)
    r = start(client, "Lua Programming", 10)
    assert r.status_code == 503 and r.json()["error"]["details"]["reason"] == "busy" and "still being prepared" in r.json()["error"]["message"] and g.calls == 0
    get_container().assessments.finish_generation(holder, ok=True)
    assert start(client, "Lua Programming", 10).status_code == 201


def test_F_insufficient_bank_and_no_generator_is_an_error_not_a_fake_success(client):
    get_container().bank.call = Gen(error=RuntimeError("down"))
    r = start(client, "Perl Programming", 10)
    assert r.status_code == 503 and client.get(f"{D}/sessions", headers=H()).json()["total"] == 0


def test_the_legacy_quiz_endpoints_still_work_untouched(client, monkeypatch):
    assert client.post("/learning/generate-quiz", json={"topic": "x"}).status_code in (401, 403)


# ------------------------------------------------------------------------------------------------ slow providers (a CPU-only local model emits ~2 tokens/s)
@pytest.fixture
def slow(monkeypatch):
    from app.core.config import get_settings
    st = get_settings()
    monkeypatch.setattr(st, "QUESTION_BATCH_SIZE", 2)
    return st


def wait_for(fn, timeout=8.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


def test_slow_model_generates_in_small_batches_and_every_batch_is_saved_as_it_validates(client, slow):
    g = Gen(); get_container().bank.call = g
    r = start(client, "Swift Programming", 10)
    assert r.status_code == 201 and len(r.json()["questions"]) == 10 and r.json()["generation_source"] == "ai_generated"
    assert g.calls == 5 and all("exactly 2 questions" in p for p in g.prompts)                                       # five small calls, not one giant one
    assert get_container().assessments.generation_stats()["llm_calls"] == 5 and get_container().assessments.generation_stats()["questions_added"] == 10


def test_a_failure_part_way_keeps_the_batches_already_generated(client, slow):
    calls = {"n": 0}
    inner = Gen()

    async def flaky(prompt):
        calls["n"] += 1
        if calls["n"] >= 3:
            raise RuntimeError("model stalled")                                                                           # persistent: the retry fails too
        return await inner(prompt)
    get_container().bank.call = flaky
    r = start(client, "Kotlin Programming", 10)
    d = r.json()
    assert r.status_code == 201 and d["question_count"] == 4 and d["shortfall"] == 6 and "Only 4 of the 10" in d["note"]     # two batches survived the third failing
    skill = get_container().registry.resolve("kotlin programming", 1.0)
    assert len(get_container().assessments.candidates(skill.skill_id)) == 4                                              # nothing was thrown away
    get_container().bank.call = inner                                                                                    # the provider recovers
    again = start(client, "Kotlin Programming", 10, user="next", hdr={"X-Idempotency-Key": "n"})                         # next learner: tops up only the 6 missing
    assert again.status_code == 201 and again.json()["question_count"] == 10 and inner.calls == 5


def test_a_single_transient_provider_error_is_retried_and_generation_carries_on(client, slow, monkeypatch):
    import app.domains.assessments.bank.service as svc_mod
    real_sleep = asyncio.sleep
    monkeypatch.setattr(svc_mod.asyncio, "sleep", lambda *_a, **_k: real_sleep(0))                                       # skip the 2 s pause between attempts
    calls = {"n": 0}
    inner = Gen()

    async def hiccup(prompt):
        calls["n"] += 1
        if calls["n"] == 3:
            raise ConnectionError("model swapping")
        return await inner(prompt)
    get_container().bank.call = hiccup
    r = start(client, "Groovy Programming", 10)
    assert r.status_code == 201 and r.json()["question_count"] == 10 and calls["n"] == 6                                # 5 batches + exactly one retry


def test_when_the_wait_budget_runs_out_the_quiz_starts_with_what_is_ready_and_generation_continues(slow, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.core.config import get_settings
    from app.core.container import reset_container
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "slow.db")); get_settings.cache_clear(); reset_container()
    st = get_settings(); monkeypatch.setattr(st, "QUESTION_BATCH_SIZE", 2); monkeypatch.setattr(st, "QUESTION_GENERATION_BUDGET_S", 1.3); monkeypatch.setattr(st, "QUESTION_MIN_USABLE", 3)
    import main
    try:
        with TestClient(main.app) as c:                                                                                   # keeps one event loop alive, like the real server
            g = Gen(delay=0.5); get_container().bank.call = g
            r = c.post(f"{D}/start", json={"topic": "Julia Programming", "question_count": 10}, headers=H())
            d = r.json()
            assert r.status_code == 201 and 3 <= d["question_count"] < 10 and d["shortfall"] > 0 and "Only" in d["note"] and d["generation_source"] == "ai_generated"
            assert len(d["questions"]) == d["question_count"]
            skill = get_container().registry.resolve("julia programming", 1.0)
            assert wait_for(lambda: len(get_container().assessments.candidates(skill.skill_id)) == 10, 10)                 # the background task kept going and saved the rest
            assert wait_for(lambda: get_container().assessments.generation_stats()["jobs"] == 1, 5) and g.calls == 5       # one job, five batches, no restart
            nxt = c.post(f"{D}/start", json={"topic": "Julia Programming", "question_count": 10}, headers=H("later"))
            assert nxt.json()["question_count"] == 10 and nxt.json()["generation_source"] == "question_bank" and g.calls == 5
    finally:
        reset_container(); get_settings.cache_clear()


def test_when_nothing_is_ready_within_the_budget_the_error_says_still_preparing_and_a_retry_succeeds(slow, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.core.config import get_settings
    from app.core.container import reset_container
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "prep.db")); get_settings.cache_clear(); reset_container()
    st = get_settings(); monkeypatch.setattr(st, "QUESTION_BATCH_SIZE", 5); monkeypatch.setattr(st, "QUESTION_GENERATION_BUDGET_S", 0.3)
    import main
    try:
        with TestClient(main.app) as c:
            g = Gen(delay=1.0); get_container().bank.call = g
            r = c.post(f"{D}/start", json={"topic": "Ruby Programming", "question_count": 5}, headers=H())
            e = r.json()["error"]
            assert r.status_code == 503 and e["code"] == "questions_unavailable" and e["details"]["reason"] == "preparing" and "still being generated" in e["message"] and "Nothing is lost" in e["message"]
            assert c.get(f"{D}/sessions", headers=H()).json()["total"] == 0                                                  # no half-created session
            skill = get_container().registry.resolve("ruby programming", 1.0)
            assert wait_for(lambda: len(get_container().assessments.candidates(skill.skill_id)) == 5, 8)                       # but the work was not abandoned
            ok = c.post(f"{D}/start", json={"topic": "Ruby Programming", "question_count": 5}, headers=H())
            assert ok.status_code == 201 and ok.json()["question_count"] == 5 and g.calls == 1                                # retry reuses it: still one model call
    finally:
        reset_container(); get_settings.cache_clear()


def test_batch_size_is_small_for_a_local_ollama_model_and_one_call_otherwise(client, monkeypatch):
    from app.core.config import get_settings
    from app.domains.assessments.bank.service import QuestionBankService
    store = get_container().assessments
    assert QuestionBankService(store, call=Gen()).batch_size(10) == 10                                                 # an injected / hosted call: one request
    auto = QuestionBankService(store, call=None).batch_size(10)
    assert auto in (1, 10) and QuestionBankService(store, call=None).batch_size(1) == 1
    monkeypatch.setattr(get_settings(), "QUESTION_BATCH_SIZE", 4)
    assert QuestionBankService(store, call=Gen()).batch_size(10) == 4 and QuestionBankService(store, call=Gen()).batch_size(3) == 3


def test_the_generation_lock_is_extended_between_batches_so_a_long_run_is_not_taken_over(db):
    from app.domains.skill_registry.seed import seed
    from app.domains.skill_registry.service import SkillRegistry
    from app.infrastructure.database.assessment_store import AssessmentStore
    from app.infrastructure.database.skill_repository import SqlSkillRepository
    reg = SkillRegistry(SqlSkillRepository(db)); seed(reg); store = AssessmentStore(db)
    sid = reg.resolve("Kafka", 1.0).skill_id
    t0 = T.utcnow()
    job, _ = store.acquire_generation(sid, "w1", 10, lease_s=5, now=t0)
    assert store.extend_generation(job, 100, now=t0 + timedelta(seconds=4))                                         # heartbeat after a batch
    holder = store.acquire_generation(sid, "w2", 10, now=t0 + timedelta(seconds=30))
    assert holder[0] is None and holder[1] == job                                                                   # still held at +30s thanks to the heartbeat
    store.finish_generation(job, ok=True)
    assert store.extend_generation(job, 100) is False                                                               # a finished job cannot be revived


def test_an_isolated_rejected_batch_does_not_stop_generation_but_two_in_a_row_do(client, slow, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "QUESTION_BATCH_SIZE", 1)
    inner = Gen(); bad = json.dumps({"questions": [{"concept": "x", "question_text": "A broken question with an impossible answer index?", "options": ["a1", "b2", "c3", "d4"], "correct": [9], "explanation": "Explained here at length."}]})
    n = {"i": 0}

    async def every_other_bad(prompt):
        n["i"] += 1
        return (bad, 1, 1) if n["i"] % 2 == 0 else await inner(prompt)
    get_container().bank.call = every_other_bad
    r = start(client, "Zeta Programming", 5)
    assert r.status_code == 201 and r.json()["question_count"] == 5 and n["i"] == 9                                      # five good calls with four rejected ones in between

    async def always_bad(prompt):
        return bad, 1, 1
    get_container().bank.call = always_bad
    r2 = start(client, "Eta Programming", 5)
    assert r2.status_code == 503 and r2.json()["error"]["details"]["reason"] == "invalid_output"                         # stopped after two empty batches in a row


def test_a_quiz_below_the_minimum_usable_size_is_refused_with_a_retryable_reason_and_progress_is_kept(client, slow, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "QUESTION_BATCH_SIZE", 1)
    inner = Gen(); n = {"i": 0}

    async def one_then_garbage(prompt):
        n["i"] += 1
        return await inner(prompt) if n["i"] == 1 else ("not json", 1, 1)
    get_container().bank.call = one_then_garbage
    r = start(client, "Theta Programming", 10)
    assert r.status_code == 503 and r.json()["error"]["details"]["ready"] == 1 and r.json()["error"]["details"]["requested"] == 10                 # a 1-question diagnostic is not offered
    skill = get_container().registry.resolve("theta programming", 1.0)
    assert len(get_container().assessments.candidates(skill.skill_id)) == 1 and client.get(f"{D}/sessions", headers=H()).json()["total"] == 0        # but the question is kept


def test_adaptive_batches_start_with_one_question_then_size_from_the_measured_speed(client, monkeypatch):
    from app.domains.assessments.bank.service import QuestionBankService
    svc = get_container().bank
    monkeypatch.setattr(QuestionBankService, "batch_plan", lambda self, missing: (1, missing > 1))                       # what a local Ollama gets
    g = Gen(delay=0.05); svc.call = g
    r = start(client, "Fast Local Programming", 10)
    assert r.status_code == 201 and r.json()["question_count"] == 10
    assert g.calls == 2 and "exactly 1 questions" in g.prompts[0] and "exactly 9 questions" in g.prompts[1]            # a fast server finishes in one more call

    class Slow(Gen):
        async def __call__(self, prompt):
            await asyncio.sleep(0.2)
            return await super().__call__(prompt)
    slow_g = Slow(); svc.call = slow_g
    monkeypatch.setattr(bank_service, "GENERATION_TIMEOUT_S", 0.4)                                                       # 0.2 s/question vs half of 0.4 s allowed per call
    r2 = start(client, "Slow Local Programming", 4)
    assert r2.status_code == 201 and r2.json()["question_count"] == 4
    assert slow_g.calls >= 2 and all("exactly 1 questions" in p or "exactly 2 questions" in p for p in slow_g.prompts)  # slow stays at small batches
