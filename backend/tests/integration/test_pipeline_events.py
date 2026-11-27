import asyncio
import json

import pytest

from services import content_generation as cg
from services.quiz_pipeline import run_quiz_pipeline
from tests.conftest import make_token
from tests.unit.test_content_generation import good_output

GOOD_QUIZ = json.dumps({"questions": [{"question": f"Q{i}?", "options": ["a", "b", "c", "d"], "correct_answer": "b"} for i in range(10)]})
BAD_QUIZ = json.dumps({"questions": [{"question": "only one", "options": ["a"], "correct_answer": "a"}]})


async def collect(it):
    return [e async for e in it]


def by_stage(events):
    out = {}
    for e in events:
        if e["event"] == "stage":
            out.setdefault(e["data"]["id"], []).append(e["data"])
    return out


# ---------------- quiz pipeline ----------------
async def test_quiz_pipeline_emits_real_checkpoints_in_order():
    async def gen(prompt): return GOOD_QUIZ
    ev = await collect(run_quiz_pipeline("Python", sign=lambda qs: "tok", generate_raw=gen))
    assert ev[0]["event"] == "meta" and [s["id"] for s in ev[0]["data"]["stages"]] == ["plan", "generate", "review", "sign"]
    st = by_stage(ev)
    assert [x["status"] for x in st["plan"]] == ["running", "done"]
    assert [x["status"] for x in st["generate"]] == ["running", "done"] and "10 questions returned, 10 usable" in st["generate"][-1]["detail"]
    assert st["review"][-1]["status"] == "done" and st["sign"][-1]["status"] == "done"
    assert all("duration_ms" in v[-1] for v in st.values())                      # timings come from real execution
    assert ev[-1]["event"] == "done" and ev[-1]["data"]["quiz"]["quiz_token"] == "tok" and len(ev[-1]["data"]["quiz"]["questions"]) == 10
    # order: each stage starts only after the previous one finished
    order = [e["data"]["id"] for e in ev if e["event"] == "stage" and e["data"]["status"] == "running"]
    assert order == ["plan", "generate", "review", "sign"]


async def test_quiz_review_feedback_triggers_one_bounded_revision():
    prompts = []

    async def gen(prompt):
        prompts.append(prompt)
        return BAD_QUIZ if len(prompts) == 1 else GOOD_QUIZ
    ev = await collect(run_quiz_pipeline("Python", sign=lambda qs: "tok", generate_raw=gen))
    st = by_stage(ev)
    assert [x["status"] for x in st["generate"]] == ["running", "done", "retrying", "done"] and st["generate"][2]["attempt"] == 2
    assert [x["status"] for x in st["review"]] == ["running", "failed", "running", "done"]
    assert "previous attempt was rejected" in prompts[1] and "only 0 valid questions" in prompts[1]
    assert ev[-1]["event"] == "done"


async def test_quiz_pipeline_gives_up_after_the_bound_with_a_clear_error():
    calls = []

    async def gen(prompt): calls.append(1); return BAD_QUIZ
    ev = await collect(run_quiz_pipeline("Python", sign=lambda qs: "tok", generate_raw=gen))
    assert len(calls) == 2 and ev[-1]["event"] == "error" and "usable quiz" in ev[-1]["data"]["message"]
    assert "sign" not in by_stage(ev) or by_stage(ev)["sign"][-1]["status"] != "done"


async def test_quiz_model_failure_marks_generate_failed_without_leaking_details():
    async def gen(prompt): raise RuntimeError("secret internal error: key=hunter2")
    ev = await collect(run_quiz_pipeline("Python", sign=lambda qs: "tok", generate_raw=gen))
    st = by_stage(ev)
    assert st["generate"][-1]["status"] == "failed" and ev[-1]["event"] == "error"
    assert "hunter2" not in json.dumps(ev)


async def test_quiz_pipeline_cancellation_stops_the_model_call():
    started, cancelled = asyncio.Event(), []

    async def gen(prompt):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True); raise
    it = run_quiz_pipeline("Python", sign=lambda qs: "tok", generate_raw=gen)
    task = asyncio.create_task(it.__anext__())            # meta
    await task
    nxt = asyncio.create_task(it.__anext__()); await started.wait()
    nxt.cancel(); await asyncio.gather(nxt, return_exceptions=True)
    await it.aclose()
    assert cancelled


# ---------------- content pipeline ----------------
async def test_section_stage_events_show_retry_then_done_and_failure():
    from services.pipeline import Tracker
    q = asyncio.Queue(); t = Tracker(q); calls = {"n": 0}

    async def flaky(sec, prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("x")
        return good_output(sec), False
    v, ok = await cg.generate_section(cg.SECTIONS[7], "Python", "Beginner", 3, flaky, tracker=t)
    evs = [q.get_nowait()["data"] for _ in range(q.qsize())]
    assert ok and [e["status"] for e in evs] == ["running", "retrying", "done"] and evs[1]["attempt"] == 2 and evs[2]["detail"] == "8 items"

    async def dead(sec, prompt): raise RuntimeError("x")
    q2 = asyncio.Queue(); await cg.generate_section(cg.SECTIONS[0], "Python", "Beginner", 3, dead, tracker=Tracker(q2))
    last = [q2.get_nowait()["data"] for _ in range(q2.qsize())][-1]
    assert last["status"] == "failed" and last["attempt"] == 2 and "regenerated" in last["detail"]


@pytest.fixture
def model(monkeypatch):
    async def call(sec, prompt): return good_output(sec), False
    monkeypatch.setattr(cg, "_call_model", call)
    monkeypatch.setattr(cg, "cache_key", lambda t, l, s: f"v1:study-content:1:shared:{t.lower()}:{l}:{s}")


def sse(text):
    return [(b.split("\n", 1)[0][7:], json.loads(b.split("\n", 1)[1][6:])) for b in text.strip().split("\n\n")]


def test_content_stream_pipeline_checkpoints_end_to_end(client, model):
    tok = client.post("/auth/register", json={"name": "Pipe User", "email": "p@example.com", "password": "password-123"}).json()["access_token"]
    ev = sse(client.post("/learning/generate-content/stream", json={"topic": "Python", "level": "Beginner", "score": 3},
                         headers={"Authorization": f"Bearer {tok}"}).text)
    stages = [d for e, d in ev if e == "stage"]
    final = {}
    for d in stages: final[d["id"]] = d
    assert final["plan"]["status"] == "done" and final["review"]["status"] == "done" and final["save"]["status"] == "done"
    assert all(final[f"section:{n}"]["status"] == "done" for n in cg.SECTION_NAMES)
    assert "cached for reuse" in final["save"]["detail"] and "history updated" in final["save"]["detail"]
    ids = [d["id"] for d in stages]
    assert ids.index("plan") < ids.index("section:summary") < ids.index("review") < ids.index("save")
    assert ev[-1][0] == "done" and ev[-1][1]["elapsed_ms"] >= 0


def test_quiz_stream_endpoint_contract_and_auth(client, monkeypatch):
    import services.gemini_service as gs

    async def fake_generate(prompt, temperature=0.7, response_mime_type=None): return GOOD_QUIZ
    monkeypatch.setattr(gs, "_generate", fake_generate)
    assert client.post("/learning/generate-quiz/stream", json={"topic": "Python"}).status_code in (401, 403)
    r = client.post("/learning/generate-quiz/stream", json={"topic": "Python"}, headers={"Authorization": f"Bearer {make_token('u1')}"})
    ev = sse(r.text)
    assert r.headers["content-type"].startswith("text/event-stream") and ev[0][0] == "meta" and ev[-1][0] == "done"
    quiz = ev[-1][1]["quiz"]
    assert set(quiz) == {"topic", "questions", "quiz_token"} and len(quiz["questions"]) == 10
    # the token from the stream grades correctly
    sub = client.post("/learning/submit-quiz", json={"topic": "Python", "answers": {str(i): 1 for i in range(10)}, "questions": [], "quiz_token": quiz["quiz_token"]},
                      headers={"Authorization": f"Bearer {make_token('u1')}"})
    assert sub.status_code == 200 and sub.json()["score"] == 10
