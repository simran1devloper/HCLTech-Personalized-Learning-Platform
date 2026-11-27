"""Assessment generation lifecycle: exact counts, bank-first, only-the-missing generation, incremental persistence, replayable SSE, cancellation, de-duplication, recovery."""
import asyncio
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.core.container import get_container
from tests.conftest import make_token
from tests.integration.test_assessment_bank_service import FakeCall, fake_json
from tests.integration.test_assessment_flow_api import A, H, start, wait_done

pytestmark = pytest.mark.usefixtures("live_client")


def events(client, aid, **kw):
    r = client.get(f"{A}/{aid}/events" + (f"?after={kw['after']}" if "after" in kw else ""), headers=H(**kw.get("hdr", {})))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    out = []
    for block in r.text.split("\n\n"):
        lines = dict(l.split(": ", 1) for l in block.split("\n") if l and not l.startswith(":") and ": " in l)
        if "event" in lines:
            out.append((int(lines["id"]), lines["event"], json.loads(lines["data"])))
    return out


class Varied(FakeCall):
    """Like FakeCall, but every call writes different questions (a real model does not repeat itself word for word)."""
    def __init__(self, payload=None):
        super().__init__(payload)

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        n = int(prompt.split("exactly ")[1].split(" ")[0])
        return (self.payload if self.payload is not None else fake_json(n, base=f"Call{len(self.prompts)}")), 100, 200


class Gated(Varied):
    """Answers the first call at once and blocks later calls until released (a slow model): lets a test look at the state *between* batches."""
    def __init__(self, release_after=1):
        super().__init__()
        self.gate, self.release_after = threading.Event(), release_after

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        n = int(prompt.split("exactly ")[1].split(" ")[0])
        if len(self.prompts) > self.release_after:
            while not self.gate.is_set():
                await asyncio.sleep(0.02)
        return fake_json(n, base=f"Call{len(self.prompts)}"), 10, 20


@pytest.fixture
def batch1(monkeypatch):
    monkeypatch.setattr(get_settings(), "QUESTION_BATCH_SIZE", 1)


def wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if (v := cond()):
            return v
        time.sleep(0.03)
    raise AssertionError("condition not reached")


# ------------------------------------------------------------------------------------------------ exact counts
@pytest.mark.parametrize("n", [1, 5, 10])
def test_requested_count_is_respected_end_to_end(live_client, n):
    call = Varied()
    get_container().bank.call = call
    r = start(live_client, "Kafka", n)
    assert r.status_code == 202 and r.json()["requested"] == n
    aid = r.json()["assessment_id"]
    st = wait_done(live_client, aid)
    assert st["status"] == "completed" and st["completed"] == n and len(st["questions"]) == n and st["progress"] == 100.0
    got = live_client.get(f"{A}/{aid}", headers=H()).json()
    assert got["total_questions"] == n and len(got["questions"]) == n and got["generation_status"] == "completed"
    assert len({q["question_id"] for q in got["questions"]}) == n                      # no duplicates
    assert len(call.prompts) == 1 and f"exactly {n} questions" in call.prompts[0]      # one batched call, not one per question
    assert "correct" not in json.dumps(st)                                              # events and state never carry the key


def test_existing_bank_is_served_instantly_without_generation_or_model(live_client):
    call = Varied()
    get_container().bank.call = call
    r = start(live_client, "Python", 10)
    assert r.status_code == 201 and len(r.json()["questions"]) == 10 and call.prompts == []
    assert live_client.get(f"{A}/{r.json()['assessment_id']}/events", headers=H()).status_code == 404   # no generation happened


def test_partial_bank_generates_only_the_missing_ones_and_excludes_recently_attempted(live_client):
    call = Varied()
    get_container().bank.call = call
    first = start(live_client, "Python", 10).json()                                    # the learner has just seen 10 of the 18
    seen = {q["question_id"] for q in first["questions"]}
    r = start(live_client, "Python", 10, hdr={"X-Idempotency-Key": "two"})             # 8 unseen remain -> 2 missing... but recent ones are only *avoided*, never required to be new
    assert r.status_code == 201 and len(r.json()["questions"]) == 10 and call.prompts == []
    assert len(seen & {q["question_id"] for q in r.json()["questions"]}) == 2          # exactly the unavoidable repeats
    d = start(live_client, "Python", 20, hdr={"X-Idempotency-Key": "three"}).json()
    assert d["total_questions"] == 18 and d["requested"] == 20
    wait_done(live_client, d["assessment_id"])
    assert len(call.prompts) == 1 and "exactly 2 questions" in call.prompts[0]


def test_progress_events_are_ordered_persisted_and_exact(live_client, batch1):
    get_container().bank.call = Varied()
    d = start(live_client, "Kafka", 4).json()
    aid = d["assessment_id"]
    wait_done(live_client, aid)
    evs = events(live_client, aid)
    seqs = [e[0] for e in evs]
    assert seqs == list(range(1, len(seqs) + 1))                                       # contiguous, strictly increasing
    types = [e[1] for e in evs]
    assert types[0] == "generation_started" and types[-1] == "completed"
    saved = [e for e in evs if e[1] == "question_saved"]
    assert [e[2]["completed"] for e in saved] == [1, 2, 3, 4] and all(e[2]["requested"] == 4 for e in saved)
    for _, t, p in evs:
        assert p["assessment_id"] == aid and p["requested"] == 4 and "timestamp" in p and p["status"] in {"generating", "completed"}
        assert p["progress"] == pytest.approx(100 * p["completed"] / 4, abs=0.1)         # saved / requested: never estimated
    for i, (_, t, p) in enumerate(evs):                                                 # a question is announced as generated -> validated -> saved, in that order
        if t == "question_saved":
            prev = [x[1] for x in evs[:i]]
            assert prev.count("question_generated") >= p["completed"] and prev.count("question_validated") >= p["completed"]
    assert evs[-1][2]["completed"] == 4 and evs[-1][2]["progress"] == 100.0


def test_each_question_is_persisted_the_moment_it_is_saved(live_client, batch1):
    call = Gated()
    get_container().bank.call = call
    aid = start(live_client, "Kafka", 3).json()["assessment_id"]
    st = wait_for(lambda: (s := live_client.get(f"{A}/{aid}/generation", headers=H()).json())["completed"] >= 1 and s)
    assert st["status"] == "generating" and st["completed"] == 1 and 0 < st["progress"] < 100      # visible while the model is still working on the rest
    assert len(get_container().assessments.attempt_questions(aid)) == 1                             # and really in the database
    live_client.put(f"{A}/{aid}/answers", json={"answers": {}}, headers=H())
    assert live_client.post(f"{A}/{aid}/submit", json={"answers": {}}, headers=H()).status_code == 409   # cannot be submitted half-generated
    call.gate.set()
    assert wait_done(live_client, aid)["completed"] == 3


# ------------------------------------------------------------------------------------------------ bad model output
def test_invalid_llm_response_fails_cleanly_and_keeps_nothing_broken(live_client):
    get_container().bank.call = Varied(payload="I am sorry, here are some questions: 1) ...")
    aid = start(live_client, "Kafka", 5).json()["assessment_id"]
    st = wait_done(live_client, aid)
    assert st["status"] == "failed" and st["error"] == "invalid_output" and st["completed"] == 0
    last = events(live_client, aid)[-1]
    assert last[1] == "error" and last[2]["status"] == "failed" and last[2]["reason"] == "invalid_output"
    assert live_client.get(A, headers=H()).json()["total"] == 0
    retry = start(live_client, "Kafka", 5, hdr={"X-Idempotency-Key": "retry"})             # a new Start after a failure is a new generation, not stuck
    assert retry.status_code == 202 and retry.json()["assessment_id"] != aid


def test_partial_and_invalid_questions_are_replaced_individually(live_client):
    good = json.loads(fake_json(5, base="First"))
    bad = {**good[0], "question_text": "Broken question without a correct option, is it?", "correct": [9], "options": ["a", "b", "c", "d"]}
    calls = []

    async def call(prompt):
        calls.append(prompt)
        n = int(prompt.split("exactly ")[1].split(" ")[0])
        if len(calls) == 1:
            return json.dumps(good[:3] + [bad]), 10, 10                                    # asked 5: 3 valid + 1 invalid (+ 1 missing)
        return fake_json(n, base="Second"), 10, 10
    get_container().bank.call = call
    aid = start(live_client, "Kafka", 5).json()["assessment_id"]
    st = wait_done(live_client, aid)
    assert st["status"] == "completed" and st["completed"] == 5
    assert len(calls) == 2 and "exactly 2 questions" in calls[1]                            # only the 2 missing (1 invalid + 1 never returned)
    evs = events(live_client, aid)
    assert [e for e in evs if e[1] == "question_validated" and e[2]["valid"] is False and e[2]["reason"] == "invalid"]


def test_duplicate_generated_questions_are_detected_and_replaced(live_client):
    one = json.loads(fake_json(1, base="Same"))[0]
    calls = []

    async def call(prompt):
        calls.append(prompt)
        n = int(prompt.split("exactly ")[1].split(" ")[0])
        return (json.dumps([one, one, one]) if len(calls) == 1 else fake_json(n, base="Other")), 5, 5
    get_container().bank.call = call
    aid = start(live_client, "Kafka", 3).json()["assessment_id"]
    st = wait_done(live_client, aid)
    assert st["status"] == "completed" and st["completed"] == 3 and len({q["question_id"] for q in st["questions"]}) == 3
    assert len(calls) == 2 and "exactly 2 questions" in calls[1]


# ------------------------------------------------------------------------------------------------ cancellation, duplicate Start, reconnect, recovery
def test_cancel_stops_generation_and_keeps_saved_questions(live_client, batch1):
    call = Gated()
    get_container().bank.call = call
    aid = start(live_client, "Kafka", 5).json()["assessment_id"]
    wait_for(lambda: live_client.get(f"{A}/{aid}/generation", headers=H()).json()["completed"] >= 1)
    r = live_client.post(f"{A}/{aid}/generation/cancel", headers=H())
    assert r.status_code == 200
    st = wait_done(live_client, aid)
    assert st["status"] == "cancelled" and st["completed"] == 1
    last = events(live_client, aid)[-1]
    assert last[1] == "cancelled" and last[2]["status"] == "cancelled" and last[2]["completed"] == 1
    call.gate.set()
    time.sleep(0.3)
    assert live_client.get(f"{A}/{aid}/generation", headers=H()).json()["completed"] == 1        # nothing is added after cancelling
    assert live_client.post(f"{A}/{aid}/generation/cancel", headers=H()).status_code == 200      # idempotent
    assert get_container().bank.generation_progress(get_container().assessments.attempt_by_id(aid)["skill_id"])["state"] != "running"
    assert live_client.get(A, headers=H()).json()["total"] == 1                                    # the saved question keeps the attempt reviewable
    assert live_client.post(f"{A}/{aid}/generation/cancel", headers=H(**{})).json()["status"] == "cancelled"


def test_cancel_is_owner_only(live_client, batch1):
    call = Gated()
    get_container().bank.call = call
    aid = start(live_client, "Kafka", 3).json()["assessment_id"]
    other = {"Authorization": f"Bearer {make_token('someone-else')}"}
    assert live_client.post(f"{A}/{aid}/generation/cancel", headers=other).status_code == 404
    assert live_client.get(f"{A}/{aid}/events", headers=other).status_code == 404
    call.gate.set()
    wait_done(live_client, aid)


def test_repeated_start_reattaches_instead_of_generating_twice(live_client, batch1):
    call = Gated()
    get_container().bank.call = call
    a = start(live_client, "Kafka", 3, hdr={"X-Idempotency-Key": "k1"})
    b = start(live_client, "Kafka", 3, hdr={"X-Idempotency-Key": "k1"})                  # same key (double click / retry)
    c = start(live_client, "Kafka", 3, hdr={"X-Idempotency-Key": "k2"})                  # different key, same skill, still generating
    ids = {x.json()["assessment_id"] for x in (a, b, c)}
    assert len(ids) == 1 and a.status_code == b.status_code == c.status_code == 202 and b.json()["reattached"] and c.json()["reattached"]
    act = live_client.get(f"{A}/generations/active", headers=H()).json()["active"]            # what a refreshed page asks first
    assert act["assessment_id"] in ids and act["status"] == "generating"
    call.gate.set()
    wait_done(live_client, a.json()["assessment_id"])
    assert len(call.prompts) == 3 and live_client.get(f"{A}/generations/active", headers=H()).json() == {"active": None}   # 3 single-question calls for ONE attempt


def test_sse_reconnect_replays_only_what_was_missed(live_client, batch1):
    get_container().bank.call = Varied()
    aid = start(live_client, "Kafka", 3).json()["assessment_id"]
    wait_done(live_client, aid)
    full = events(live_client, aid)
    assert full[-1][1] == "completed"
    cut = full[3][0]
    after_param = events(live_client, aid, after=cut)
    after_header = events(live_client, aid, hdr={"Last-Event-ID": str(cut)})
    assert [e[0] for e in after_param] == [e[0] for e in full if e[0] > cut] == [e[0] for e in after_header]
    assert events(live_client, aid, after=full[-1][0]) == []                                # fully caught up: the stream just ends
    st = live_client.get(f"{A}/{aid}/generation", headers=H()).json()
    assert st["last_seq"] == full[-1][0] and st["completed"] == 3                              # state recovery agrees with the log


def test_live_stream_delivers_events_as_questions_are_saved(live_client, batch1):
    call = Gated()
    get_container().bank.call = call
    aid = start(live_client, "Kafka", 2).json()["assessment_id"]
    got, done = [], threading.Event()

    def reader():
        with live_client.stream("GET", f"{A}/{aid}/events", headers=H()) as r:
            for line in r.iter_lines():
                if line.startswith("event: "):
                    got.append(line[7:])
                    if line == "event: completed":
                        done.set()
    t = threading.Thread(target=reader, daemon=True)
    t.start()
    wait_for(lambda: "question_saved" in got)                                                 # first question arrives while the second call is still blocked
    assert "completed" not in got
    call.gate.set()
    assert done.wait(10) and got.count("question_saved") == 2


def test_orphaned_generation_is_closed_as_interrupted_and_keeps_saved_questions(live_client):
    c = get_container()
    aid, created, sel, _ = c.bank.start("learner-1", c.registry.resolve("Python", 1.0).skill_id, 20, None, "orphan", generate_missing=True)
    assert created and sel.shortfall == 2                                                    # created, but no task was ever started for it (the process "died")
    c.assessments.set_generation(aid, now=datetime.now(timezone.utc) - timedelta(hours=1))
    st = live_client.get(f"{A}/{aid}/generation", headers=H()).json()
    assert st["status"] == "failed" and st["error"] == "interrupted" and st["completed"] == 18
    assert events(live_client, aid)[-1][1] == "error"
    assert live_client.get(f"{A}/generations/active", headers=H()).json() == {"active": None}
    assert len(live_client.get(f"{A}/{aid}", headers=H()).json()["questions"]) == 18          # still reviewable / resumable with what was saved


def test_a_database_failure_while_saving_ends_in_an_error_event_not_a_hang(live_client, monkeypatch):
    get_container().bank.call = Varied()
    store = get_container().assessments
    real = store.append_question

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(store, "append_question", boom)
    aid = start(live_client, "Kafka", 3).json()["assessment_id"]
    st = wait_done(live_client, aid)
    assert st["status"] == "failed" and st["error"] == "internal"
    monkeypatch.setattr(store, "append_question", real)
    assert events(live_client, aid)[-1][1] == "error"
