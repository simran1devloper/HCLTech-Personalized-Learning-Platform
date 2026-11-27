"""AI Tutor end to end against the real API and database with a scripted model: conversations, memory, personalisation from stored
data, tools with confirmation, idempotency, cancellation, failures and authorisation."""
import asyncio
import json

import pytest

from app.api.v1 import tutor_routes
from app.core.container import get_container
from app.core.exceptions import ProviderAccountError, ProviderStallError
from app.domains.tutoring import learner_context, service, tools


class ScriptedRouter:
    def __init__(self, tokens=("Hello", " world"), error=None, delay=0.0):
        self.tokens, self.error, self.delay, self.calls = tokens, error, delay, []

    async def generate_stream(self, prompt, params=None, req=None):
        self.calls.append((prompt, params, req))
        for i, t in enumerate(self.tokens):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield t
        if self.error:
            raise self.error

    # what the model would receive, for assertions
    @property
    def system(self):
        return self.calls[-1][1].system

    @property
    def history(self):
        return list(self.calls[-1][1].history)


@pytest.fixture
def model(monkeypatch):
    holder = {"r": ScriptedRouter()}
    monkeypatch.setattr(tutor_routes, "get_router", lambda: holder["r"])
    return holder


def parse(text):
    out = []
    for block in text.strip().split("\n\n"):
        if block.startswith(":"):
            continue
        ev, data = block.split("\n", 1)
        out.append((ev[7:], json.loads(data[6:])))
    return out


def ask(client, headers, message, **extra):
    r = client.post("/api/v1/tutor/chat", json={"message": message, **extra}, headers=headers)
    assert r.status_code == 200, r.text
    return parse(r.text)


def one(events, name):
    return next(d for e, d in events if e == name)


def thread(client, headers, conv_id):
    return client.get(f"/api/v1/tutor/conversations/{conv_id}", headers=headers).json()["messages"]


def take_assessment(client, h, skill="python", answer=0, n=3):
    a = client.post("/api/v1/assessments", json={"skill": skill, "n_questions": n}, headers=h).json()
    client.post(f"/api/v1/assessments/{a['assessment_id']}/submit", json={"answers": {q["question_id"]: answer for q in a["questions"]}}, headers=h)
    return a


# ---------------- conversation and memory ----------------
def test_multi_turn_context_is_sent_as_real_turns_even_after_long_answers(client, auth, model):
    h = auth()
    model["r"] = ScriptedRouter(("Redis is an in-memory store. " * 500,))
    first = ask(client, h, "Explain Redis.")
    cid = one(first, "meta")["conversation_id"]
    ask(client, h, "How does it work internally?", conversation_id=cid)
    r = model["r"]
    assert r.calls[-1][0] == "How does it work internally?"
    assert [role for role, _ in r.history] == ["user", "assistant"] and r.history[0][1] == "Explain Redis." and "Redis" in r.history[1][1]
    assert "Current topic: Redis" in r.system
    ask(client, h, "What is the difference between Redis and Memcached?", conversation_id=cid)
    ev = ask(client, h, "Give me a Python implementation.", conversation_id=cid)
    assert one(ev, "intent")["topic"] == "Redis" and one(ev, "intent")["intent"] == "code_generation" and len(r.history) >= 2


def test_prompt_fits_the_context_window(client, auth, model):
    h = auth()
    model["r"] = ScriptedRouter(("x" * 9000,))
    cid = one(ask(client, h, "Explain Python " + "y" * 3000), "meta")["conversation_id"]
    for _ in range(4):
        ask(client, h, "And more detail about it " + "z" * 2000, conversation_id=cid)
    prompt, params, _ = model["r"].calls[-1]
    used = len(prompt) + len(params.system) + sum(len(c) for _, c in params.history)
    assert used / 3.5 + params.max_output_tokens <= 4096 and params.history


def test_events_metadata_and_followups(client, auth, model):
    h = auth()
    ev = ask(client, h, "Explain Redis")
    names = [e for e, _ in ev]
    assert names[0] == "meta" and names[-1] == "done" and ["processing", "retrieving_context", "generating"] == [d["stage"] for e, d in ev if e == "status"]
    assert one(ev, "intent")["intent"] == "concept_explanation" and one(ev, "done")["followups"]
    reply = thread(client, h, one(ev, "meta")["conversation_id"])[-1]
    assert reply["status"] == "complete" and reply["meta"]["intent"] == "concept_explanation" and reply["meta"]["followups"]
    usage = client.get("/api/v1/tutor/usage", headers=h).json()
    assert usage["replies_by_status"] == {"complete": 1} and usage["answer_success_rate"] == 1.0 and usage["input_tokens"] > 0
    assert usage["estimated_cost_usd"] is None and usage["avg_first_token_ms"] is not None
    assert client.get("/api/v1/tutor/usage", headers=auth("bob")).json()["conversations"] == 0


# ---------------- personalisation from stored data ----------------
def test_same_question_is_personalised_by_career_goal_and_proficiency(client, auth, model):
    systems = {}
    for user, role, title in (("de", "data-engineer", "Data Engineer"), ("be", "python-backend-engineer", "Python Backend Engineer")):
        h = auth(user)
        client.post("/api/v1/goals", json={"role_slug": role}, headers=h)
        ev = ask(client, h, "Explain Kafka")
        assert {s["kind"] for s in one(ev, "context")["sources"]} == {"goal"}
        systems[user] = model["r"].system
        assert f"Career goal: {title}" in systems[user]
    assert "Data Engineer" not in systems["be"] and "Python Backend" not in systems["de"]
    # proficiency comes from real assessment evidence
    strong, weak = auth("strong"), auth("weak")
    take_assessment(client, strong, answer=0)
    take_assessment(client, weak, answer=3)
    ask(client, strong, "Explain Python decorators")
    a = model["r"].system
    ask(client, weak, "Explain Python decorators")
    b = model["r"].system
    stored = client.get("/api/v1/proficiency", headers=strong).json()["items"][0]["proficiency_score"]
    assert f"Python: proficiency {stored:.1f}/5" in a and "Python: proficiency" in b


def test_header_controls_and_context_only_when_relevant(client, auth, model):
    h = auth()
    client.post("/api/v1/goals", json={"role_slug": "ai-engineer"}, headers=h)
    take_assessment(client, h)
    ev = ask(client, h, "What is the capital of France?")
    assert "Career goal" not in model["r"].system and not [e for e, _ in ev if e == "context"]
    ask(client, h, "Explain Redis", career_goal="devops-engineer", level="beginner", language="hinglish", mode="learn")
    s = model["r"].system
    assert "DevOps Engineer" in s and "AI Engineer" not in s and "beginner" in s and "Hinglish" in s
    ask(client, h, "Explain how closures work", skill="python")
    assert "Python: proficiency" in model["r"].system


def test_missing_data_is_stated_not_invented(client, auth, model):
    h = auth()
    ask(client, h, "Create a 30-day learning plan for Kafka")
    s = model["r"].system
    assert "No career goal is set" in s and "no roadmap yet" in s


def test_context_failure_degrades_to_a_general_answer(client, auth, model, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("database is locked secret=hunter2")
    monkeypatch.setattr(learner_context, "gather", broken)
    ev = ask(client, auth(), "Explain Redis")
    assert one(ev, "context")["unavailable"] is True and ev[-1][0] == "done" and "hunter2" not in json.dumps(ev)


# ---------------- tools ----------------
def test_skill_gap_analysis_shows_real_data(client, auth, model):
    h = auth()
    ev = ask(client, h, "Analyze my skill gaps")
    assert "career goal" in one(ev, "token")["text"] and not model["r"].calls            # no goal: asked for, no model call, nothing invented
    client.post("/api/v1/goals", json={"role_slug": "ai-engineer"}, headers=h)
    ev = ask(client, h, "Analyze my skill gaps")
    t = one(ev, "tool_result")
    assert t["status"] == "succeeded" and t["tool"] == "analyze_skill_gaps"
    expected = client.get("/api/v1/skill-gaps?role=ai-engineer", headers=h).json()["items"]
    assert [i["name"] for i in t["result"]["data"]["items"]] == [i["name"] for i in expected[:8]]
    assert all(i["current"] is None and i["assessed"] is False for i in t["result"]["data"]["items"])   # no evidence: unknown, not zero
    assert "has not been assessed" in model["r"].system and " is 0.0" not in model["r"].system
    assert "UNTRUSTED_CONTENT" in model["r"].system and expected[0]["name"] in model["r"].system
    assert thread(client, h, one(ev, "meta")["conversation_id"])[-1]["tools"][0]["result"]["kind"] == "skill_gaps"


def test_assessment_review(client, auth, model):
    h = auth()
    ev = ask(client, h, "Explain my previous assessment")
    assert "No completed assessment" in one(ev, "token")["text"] and not model["r"].calls
    a = take_assessment(client, h)
    ev = ask(client, h, "Explain my previous assessment")
    card = one(ev, "tool_result")["result"]
    assert card["link"] == f"/assessments/{a['assessment_id']}" and card["data"]["total_questions"] == 3
    assert "Concept '" in model["r"].system


@pytest.mark.parametrize("message,tool", [("Generate a Python quiz with 4 questions", "create_quiz"), ("Create a roadmap for Data Engineer", "generate_roadmap")])
def test_actions_need_confirmation_and_run_once(client, auth, model, message, tool):
    h = auth("alice")
    ev = ask(client, h, message)
    ex = one(ev, "tool_result")
    assert ex["status"] == "pending" and ex["tool"] == tool and not model["r"].calls
    count = lambda: (client.get("/api/v1/roadmaps", headers=h).json()["items"] if tool == "generate_roadmap"
                     else client.get("/api/v1/assessments", headers=h).json()["items"])
    assert count() == []                                                                 # nothing created before confirmation
    url = f"/api/v1/tutor/actions/{ex['execution_id']}"
    assert client.post(url + "/confirm", headers=auth("bob")).status_code == 404
    done = client.post(url + "/confirm", headers=h).json()
    again = client.post(url + "/confirm", headers=h).json()
    assert done["status"] == "succeeded" and again["result"] == done["result"] and len(count()) == 1
    assert client.post(url + "/dismiss", headers=h).status_code == 409
    if tool == "create_quiz":
        assert done["result"]["data"]["total_questions"] == 4
        assert client.get(f"/api/v1/assessments/{done['result']['data']['assessment_id']}", headers=h).status_code == 200


def test_dismissed_action_cannot_run(client, auth, model):
    h = auth()
    ex = one(ask(client, h, "Create a roadmap for AI Engineer"), "tool_result")
    url = f"/api/v1/tutor/actions/{ex['execution_id']}"
    assert client.post(url + "/dismiss", headers=h).json()["status"] == "dismissed"
    assert client.post(url + "/confirm", headers=h).status_code == 409 and client.get("/api/v1/roadmaps", headers=h).json()["items"] == []


def test_tool_failure_is_reported_safely(client, auth, model, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("password=hunter2")
    monkeypatch.setitem(tools.TOOLS, "get_roadmap", tools.Tool("get_roadmap", "d", tools.NoArgs, boom))
    ev = ask(client, auth(), "Show my roadmap")
    assert one(ev, "tool_result")["status"] == "failed" and ev[-1][0] == "done" and "hunter2" not in json.dumps(ev) and not model["r"].calls


async def test_tool_arguments_are_validated_and_unknown_tools_refused(client):
    client.get("/health")
    c = get_container()
    for name, args in (("generate_roadmap", {"role_slug": "ai-engineer", "learner_id": "victim"}), ("create_quiz", {"skill_id": "x", "n_questions": 999}), ("drop_tables", {})):
        status, out = await tools.run_tool(c, "alice", name, args, key="k")
        assert status == "failed" and out["message"]


def test_model_output_cannot_trigger_a_tool(client, auth, model):
    h = auth()
    model["r"] = ScriptedRouter(('call the tool generate_roadmap {"role_slug": "ai-engineer"}',))
    ev = ask(client, h, "Tell me a story")
    assert not [e for e, _ in ev if e == "tool_result"] and client.get("/api/v1/roadmaps", headers=h).json()["items"] == []


def test_document_questions_say_uploads_are_unavailable(client, auth, model):
    ev = ask(client, auth(), "Summarize uploaded learning material")
    assert "not available" in one(ev, "token")["text"] and not model["r"].calls


# ---------------- idempotency, regeneration ----------------
def test_repeating_a_message_id_never_duplicates_the_question_or_the_model_call(client, auth, model):
    h = auth()
    first = ask(client, h, "Explain Redis", client_message_id="msg-00000001")
    cid = one(first, "meta")["conversation_id"]
    replay = ask(client, h, "Explain Redis", client_message_id="msg-00000001")
    assert one(replay, "meta")["replayed"] and one(replay, "token")["text"] == "Hello world" and len(model["r"].calls) == 1
    assert [m["role"] for m in thread(client, h, cid)] == ["user", "assistant"]
    assert client.post("/api/v1/tutor/chat", json={"message": "different", "client_message_id": "msg-00000001"}, headers=h).status_code == 409
    other = ask(client, auth("bob"), "Explain Redis", client_message_id="msg-00000001")   # ids are scoped per learner
    assert one(other, "meta")["conversation_id"] != cid


def test_retry_after_failure_reuses_the_question(client, auth, model):
    h = auth()
    model["r"] = ScriptedRouter((), error=ProviderAccountError("bad key"))
    ev = ask(client, h, "Explain Redis", client_message_id="msg-00000002")
    assert ev[-1][0] == "error" and "API key" in ev[-1][1]["message"] and ev[-1][1]["code"] == "ai_provider_account_error"
    cid = one(ev, "meta")["conversation_id"]
    model["r"] = ScriptedRouter(("recovered",))
    ask(client, h, "Explain Redis", client_message_id="msg-00000002")
    msgs = thread(client, h, cid)
    assert [(m["role"], m["content"]) for m in msgs] == [("user", "Explain Redis"), ("assistant", "recovered")]


def test_an_old_failed_question_cannot_be_replayed_over_newer_ones(client, auth, model):
    h = auth()
    model["r"] = ScriptedRouter((), error=ProviderStallError("x"))
    cid = one(ask(client, h, "first", client_message_id="msg-00000003"), "meta")["conversation_id"]
    model["r"] = ScriptedRouter(("second answer",))
    ask(client, h, "second", conversation_id=cid)
    assert client.post("/api/v1/tutor/chat", json={"message": "first", "client_message_id": "msg-00000003"}, headers=h).status_code == 409
    assert thread(client, h, cid)[-1]["content"] == "second answer"


@pytest.mark.parametrize("error,code", [(ProviderStallError("silent"), "ai_provider_stalled"), (ValueError("x secret"), "internal_error"),
                                        (RuntimeError("no LLM provider configured (check)"), "ai_not_configured")])
def test_failures_are_meaningful_and_sanitised(client, auth, model, error, code):
    model["r"] = ScriptedRouter((), error=error)
    ev = ask(client, auth(), "hi")
    assert ev[-1][0] == "error" and ev[-1][1]["code"] == code and "secret" not in ev[-1][1]["message"]


def test_empty_model_answer_is_an_error_not_a_blank_reply(client, auth, model):
    model["r"] = ScriptedRouter(())
    assert ask(client, auth(), "hi")[-1][0] == "error"


def test_regenerate_hides_the_previous_answer(client, auth, model):
    h = auth()
    cid = one(ask(client, h, "Explain Redis"), "meta")["conversation_id"]
    model["r"] = ScriptedRouter(("better",))
    r = client.post(f"/api/v1/tutor/conversations/{cid}/regenerate", json={}, headers=h)
    assert parse(r.text)[-1][0] == "done" and model["r"].history == []       # the old answer is not fed back to the model
    assert [m["content"] for m in thread(client, h, cid)] == ["Explain Redis", "better"]
    empty = client.post("/api/v1/tutor/conversations", json={}, headers=h).json()["conversation_id"]
    assert client.post(f"/api/v1/tutor/conversations/{empty}/regenerate", json={}, headers=h).status_code == 409


# ---------------- cancellation and interruption (driving the stream directly) ----------------
def _turn(c, router, question="Explain Redis", conv="conv-x"):
    c.store.create_conversation("alice", conv, question, None, "en")
    g = service.generations.claim(conv)
    mid = c.store.add_message("alice", conv, "user", question)
    return g, service.run_turn(c, "alice", g, {"message_id": mid, "content": question}, service.Options(), lambda: router)


async def _until_token(stream):
    async for ev in stream:
        if ev.startswith("event: token"):
            return ev
    raise AssertionError("no token")


async def test_stop_cancels_keeps_the_partial_answer_and_closes_the_model_stream(client):
    client.get("/health")
    c = get_container()
    closed = []

    class Endless:
        async def generate_stream(self, *a, **k):
            try:
                yield "partial "
                await asyncio.sleep(30)
            finally:
                closed.append(True)
    g, stream = _turn(c, Endless())
    await _until_token(stream)
    assert (await tutor_routes.stop(g.message_id, "alice", c)) == {"stopping": True}
    rest = [e async for e in stream]
    assert rest[-1].startswith("event: cancelled") and closed == [True]
    reply = c.tutor.thread("alice", "conv-x")[-1]
    assert (reply["status"], reply["content"]) == ("cancelled", "partial ") and service.generations.active("conv-x") is None


async def test_client_disconnect_keeps_the_partial_answer(client):
    client.get("/health")
    c = get_container()
    _, stream = _turn(c, ScriptedRouter(("a", "b", "c"), delay=0.01), conv="conv-d")
    await _until_token(stream)
    await stream.aclose()                                    # what the server does when the connection drops
    reply = c.tutor.thread("alice", "conv-d")[-1]
    assert reply["status"] == "cancelled" and reply["content"].startswith("a") and service.generations.active("conv-d") is None


async def test_silent_model_sends_keepalives_and_a_second_request_is_refused(client, monkeypatch):
    client.get("/health")
    c = get_container()
    monkeypatch.setattr(service, "HEARTBEAT_S", 0.05)
    monkeypatch.setattr(service, "POLL_S", 0.02)
    g, stream = _turn(c, ScriptedRouter(("late",), delay=0.3), conv="conv-h")
    seen = []
    async for ev in stream:
        seen.append(ev)
        if ev.startswith(": ping") and len([e for e in seen if e.startswith(": ping")]) == 1:
            with pytest.raises(Exception, match="already being generated"):
                service.generations.claim("conv-h")
    assert any(e.startswith(": ping") for e in seen) and seen[-1].startswith("event: done")


def test_a_reply_abandoned_by_a_restart_is_reported_as_interrupted(client, auth):
    h = auth("alice")
    cid = client.post("/api/v1/tutor/conversations", json={}, headers=h).json()["conversation_id"]
    c = get_container()
    c.store.add_message("alice", cid, "user", "q")
    c.store.add_message("alice", cid, "assistant", "half", status="streaming")
    assert thread(client, h, cid)[-1]["status"] == "interrupted"


# ---------------- conversation management and authorisation ----------------
def test_conversation_management(client, auth, model):
    h = auth("alice")
    a = one(ask(client, h, "Explain Redis"), "meta")["conversation_id"]
    b = one(ask(client, h, "Explain Docker"), "meta")["conversation_id"]
    ids = lambda q="": [x["conversation_id"] for x in client.get(f"/api/v1/tutor/conversations{q}", headers=h).json()["items"]]
    items = client.get("/api/v1/tutor/conversations", headers=h).json()["items"]
    assert ids() == [b, a] and items[0]["preview"] == "Hello world" and items[0]["pinned"] is False
    assert client.patch(f"/api/v1/tutor/conversations/{a}", json={"pinned": True, "title": "Caching"}, headers=h).json()["title"] == "Caching"
    assert ids() == [a, b] and ids("?q=cach") == [a] and ids("?q=docker") == [b] and ids("?q=hello") == [a, b] and ids("?q=%25") == []
    assert client.delete(f"/api/v1/tutor/conversations/{a}", headers=h).status_code == 204 and ids() == [b]
    assert client.get(f"/api/v1/tutor/conversations/{a}", headers=h).status_code == 404
    created = client.post("/api/v1/tutor/conversations", json={}, headers=h).json()
    ask(client, h, "Explain Git", conversation_id=created["conversation_id"])
    assert client.get(f"/api/v1/tutor/conversations/{created['conversation_id']}", headers=h).json()["title"] == "Explain Git"


def test_feedback(client, auth, model):
    h = auth("alice")
    ev = ask(client, h, "hi")
    meta = one(ev, "meta")
    url = f"/api/v1/tutor/messages/{meta['message_id']}/feedback"
    assert client.put(url, json={"rating": "down", "comment": "wrong"}, headers=h).status_code == 200
    assert client.put(url, json={"rating": "up"}, headers=h).status_code == 200
    assert thread(client, h, meta["conversation_id"])[-1]["feedback"] == "up"
    assert client.get("/api/v1/tutor/usage", headers=h).json()["feedback"] == {"up": 1, "down": 0}
    client.put(url, json={"rating": None}, headers=h)
    assert thread(client, h, meta["conversation_id"])[-1]["feedback"] is None
    assert client.put(f"/api/v1/tutor/messages/{meta['user_message_id']}/feedback", json={"rating": "up"}, headers=h).status_code == 404


def test_another_learner_can_do_nothing(client, auth, model):
    h, bob = auth("alice"), auth("bob")
    meta = one(ask(client, h, "my private question"), "meta")
    cid, mid = meta["conversation_id"], meta["message_id"]
    ex = one(ask(client, h, "Create a roadmap for AI Engineer", conversation_id=cid), "tool_result")["execution_id"]
    base = f"/api/v1/tutor/conversations/{cid}"
    attempts = [client.get(base, headers=bob), client.patch(base, json={"title": "x"}, headers=bob), client.delete(base, headers=bob),
                client.post(base + "/regenerate", json={}, headers=bob), client.post("/api/v1/tutor/chat", json={"message": "x", "conversation_id": cid}, headers=bob),
                client.post(f"/api/v1/tutor/messages/{mid}/stop", headers=bob), client.put(f"/api/v1/tutor/messages/{mid}/feedback", json={"rating": "up"}, headers=bob),
                client.post(f"/api/v1/tutor/actions/{ex}/confirm", headers=bob), client.post(f"/api/v1/tutor/actions/{ex}/dismiss", headers=bob)]
    assert [r.status_code for r in attempts] == [404] * len(attempts)
    assert client.get("/api/v1/tutor/conversations?q=private", headers=bob).json()["items"] == []
    assert all(client.get(base).status_code == 401 for _ in (1,)) and len(thread(client, h, cid)) == 4


def test_input_validation(client, auth, model):
    h = auth()
    for body in ({"message": "   "}, {"message": "x" * 8001}, {"message": "x", "mode": "root"}, {"message": "x", "client_message_id": "bad id !"}):
        assert client.post("/api/v1/tutor/chat", json=body, headers=h).status_code == 422, body
    assert client.post("/api/v1/tutor/chat", json={"message": "x" * 8000}, headers=h).status_code == 200


# ---------------- migration safety ----------------
def test_upgrade_keeps_existing_conversations_and_downgrade_is_clean(tmp_path):
    import sqlalchemy as sa
    from alembic import command
    from app.infrastructure.database.engine import Db
    from app.infrastructure.database.migrate import alembic_config
    db = Db(f"sqlite+pysqlite:///{tmp_path}/m.db")

    def run(fn, rev):
        with db.begin() as conn:
            fn(alembic_config(db.url, conn), rev)

    def rows():
        with db.begin() as c:
            return c.execute(sa.text("SELECT role, content FROM tutor_messages ORDER BY message_id")).all()
    run(command.upgrade, "0009")
    with db.begin() as c:
        c.execute(sa.text("INSERT INTO tutor_conversations VALUES ('c','u','t',NULL,'en','2026-01-01','2026-01-01')"))
        c.execute(sa.text("INSERT INTO tutor_messages (conversation_id, role, content, created_at) VALUES ('c','user','kept','2026-01-01')"))
    run(command.upgrade, "head")
    with db.begin() as c:
        assert c.execute(sa.text("SELECT status FROM tutor_messages")).scalar() == "complete"
    assert rows() == [("user", "kept")]
    run(command.downgrade, "0009")
    assert rows() == [("user", "kept")]
    run(command.upgrade, "head")
    assert rows() == [("user", "kept")]
    db.close()
