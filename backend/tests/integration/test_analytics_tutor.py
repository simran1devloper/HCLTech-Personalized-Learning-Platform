import json
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta, timezone

import pytest

from app.ai.types import LLMResponse
from app.core.exceptions import ProviderError
from app.domains.analytics.service import build_insights, build_summary
from app.domains.learning_intelligence.proficiency import ProficiencyEstimate


def finish_assessment(client, h, skill="python", n=3, answer=0):
    a = client.post("/api/v1/assessments", json={"skill": skill, "n_questions": n}, headers=h).json()
    client.post(f"/api/v1/assessments/{a['assessment_id']}/submit", json={"answers": {q["question_id"]: answer for q in a["questions"]}}, headers=h)
    return a


def test_empty_learner_gets_nulls_not_invented_numbers(client, auth):
    s = client.get("/api/v1/analytics/summary", headers=auth()).json()
    assert s["skills_assessed"] == 0 and s["assessments_completed"] == 0 and s["career"] is None
    assert s["learning_hours"] is None and "not tracked" in s["unavailable"]["learning_hours"]
    assert len(s["weekly_activity"]) == 12 and all(w["assessments"] == 0 for w in s["weekly_activity"])
    assert s["consistency"]["current_streak_days"] == 0 and s["roadmap_completion"]["ratio"] is None
    assert [i["kind"] for i in s["insights"]] == ["start"]


def test_summary_reflects_real_activity_and_history(client, auth):
    h = auth()
    client.post("/api/v1/goals", json={"role_slug": "ai-engineer"}, headers=h)
    finish_assessment(client, h, "python", 3)
    rm = client.post("/api/v1/roadmaps/generate", json={"role_slug": "ai-engineer"}, headers=h).json()
    s = client.get("/api/v1/analytics/summary", headers=h).json()
    assert s["skills_assessed"] == 1 and s["assessments_completed"] == 1
    assert s["weekly_activity"][-1]["assessments"] == 1 and s["weekly_activity"][-1]["questions"] == 3
    assert s["consistency"]["current_streak_days"] == 1 and len(s["accuracy_trend"]) == 1
    assert s["career"]["role_slug"] == "ai-engineer" and 0 <= s["career"]["readiness"] <= 1 and s["career"]["recommended_next"]
    tr = s["career"]["readiness_trend"]
    assert len(tr) == 1 and tr[0]["readiness"] == s["career"]["readiness"]  # one snapshot date => trend ends at current value
    assert s["roadmap_completion"]["total"] == len(rm["milestones"]) and s["roadmap_completion"]["done"] == 0
    hist = client.get("/api/v1/assessments", headers=h).json()
    assert hist["total"] == 1 and hist["items"][0]["status"] == "completed" and hist["items"][0]["skill"] == "Python"
    pid = hist["items"][0]["skill_id"]
    ph = client.get(f"/api/v1/proficiency/{pid}/history", headers=h).json()
    assert len(ph["snapshots"]) == 1 and ph["evidence"] and ph["current"]["proficiency_version"] == "prof-v1"
    assert client.get("/api/v1/roadmaps", headers=h).json()["items"][0]["roadmap_id"] == rm["roadmap_id"]
    assert len(client.get(f"/api/v1/roadmaps/{rm['roadmap_id']}/versions", headers=h).json()["items"]) == 1


def test_history_is_per_user(client, auth):
    finish_assessment(client, auth("alice"))
    assert client.get("/api/v1/assessments", headers=auth("bob")).json()["total"] == 0
    assert client.get("/api/v1/analytics/summary", headers=auth("bob")).json()["assessments_completed"] == 0
    rm = client.post("/api/v1/roadmaps/generate", json={"role_slug": "ai-engineer"}, headers=auth("alice")).json()
    assert client.get(f"/api/v1/roadmaps/{rm['roadmap_id']}/versions", headers=auth("bob")).status_code == 404
    assert client.get("/api/v1/roadmaps", headers=auth("bob")).json()["items"] == []


def test_streak_and_regression_logic_pure():
    now = datetime(2026, 6, 10, 12, tzinfo=timezone.utc)
    at = lambda d: {"completed_at": now - timedelta(days=d), "score": .5, "n_questions": 4, "skill_id": "s"}
    est = {"s": ProficiencyEstimate(skill_id="s", proficiency_score=2, confidence_score=.2, evidence_count=2, last_assessed_at=now)}
    snaps = [{"skill_id": "s", "proficiency_score": 3.0, "confidence_score": .5, "snapshot_at": (now - timedelta(days=20)).isoformat()},
             {"skill_id": "s", "proficiency_score": 2.0, "confidence_score": .2, "snapshot_at": now.isoformat()}]
    s = build_summary(attempts=[at(0), at(1), at(2), at(5)], snapshots=snaps, estimates=est, roadmaps=[], skill_names={"s": "Python"}, now=now)
    assert s["consistency"]["current_streak_days"] == 3 and s["consistency"]["active_days_last_28"] == 4
    assert s["retention"][0]["delta"] == -1.0 and s["retention"][0]["days_between"] == 20
    kinds = {i["kind"] for i in build_insights(s, None)}
    assert {"streak", "regression", "low_confidence"} <= kinds
    s2 = build_summary(attempts=[at(1)], snapshots=[], estimates={}, roadmaps=[], skill_names={}, now=now)
    assert s2["consistency"]["current_streak_days"] == 1  # streak survives until the end of "today"
    assert build_summary(attempts=[at(3)], snapshots=[], estimates={}, roadmaps=[], skill_names={}, now=now)["consistency"]["current_streak_days"] == 0


# ---------------- tutor SSE ----------------
class FakeRouter:
    def __init__(self, tokens=("Hello", " world"), fail_after=None):
        self.tokens, self.fail_after, self.prompts = tokens, fail_after, []

    async def generate_stream(self, prompt, params=None, req=None):
        # the LangChain adapter sends the system message via params.system: record both, as a provider would see them
        history = "\n".join(f"{r}: {c}" for r, c in getattr(params, "history", ()))
        self.prompts.append(f"{getattr(params, 'system', None) or ''}\n{history}\n{prompt}")
        for i, t in enumerate(self.tokens):
            if self.fail_after is not None and i == self.fail_after:
                raise ProviderError("boom secret=hunter2")
            yield t

    async def generate(self, prompt, params=None, req=None):
        self.prompts.append(prompt)
        return LLMResponse("narrated", "fake", "m")


def events(text):
    out = []
    for block in text.strip().split("\n\n"):
        if block.startswith(":"):
            continue
        ev, data = block.split("\n", 1)
        out.append((ev[7:], json.loads(data[6:])))
    return out


def core(ev):
    """The original protocol events; status / intent / context / tool events are additions around them."""
    return [(e, d) for e, d in ev if e in ("meta", "token", "done", "error")]


@pytest.fixture
def fake(monkeypatch):
    import app.api.v1.extra_routes as er
    import app.api.v1.tutor_routes as tr
    r = FakeRouter()
    monkeypatch.setattr(er, "get_router", lambda: r)
    monkeypatch.setattr(tr, "get_router", lambda: r)
    return r


def test_tutor_streams_persists_and_uses_only_own_context(client, auth, fake):
    h = auth("alice")
    finish_assessment(client, h, "python", 2)
    r = client.post("/api/v1/tutor/chat", json={"message": "explain asyncio", "skill": "python", "language": "hinglish"}, headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = core(events(r.text))
    assert [e for e, _ in ev] == ["meta", "token", "token", "done"] and "".join(d["text"] for e, d in ev if e == "token") == "Hello world"
    cid = ev[0][1]["conversation_id"]
    assert "Python: proficiency" in fake.prompts[0] and "Hinglish" in fake.prompts[0]
    conv = client.get(f"/api/v1/tutor/conversations/{cid}", headers=h).json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"] and conv["messages"][1]["content"] == "Hello world"
    # follow-up uses server-side history; another user sees none of it and cannot write into it
    client.post("/api/v1/tutor/chat", json={"message": "and await?", "conversation_id": cid}, headers=h)
    assert "explain asyncio" in fake.prompts[1] or "Hello world" in fake.prompts[1]
    b = auth("bob")
    assert client.get("/api/v1/tutor/conversations", headers=b).json()["items"] == []
    assert client.get(f"/api/v1/tutor/conversations/{cid}", headers=b).status_code == 404
    assert client.post("/api/v1/tutor/chat", json={"message": "hi", "conversation_id": cid}, headers=b).status_code == 404
    bob_ctx = client.post("/api/v1/tutor/chat", json={"message": "hi"}, headers=b)
    assert "Python: proficiency" not in fake.prompts[-1]  # alice's proficiency never reaches bob's prompt


def test_tutor_stream_error_is_sanitised_and_partial_reply_is_marked_failed(client, auth, monkeypatch):
    import app.api.v1.tutor_routes as tr
    monkeypatch.setattr(tr, "get_router", lambda: FakeRouter(("a", "b", "c"), fail_after=1))
    h = auth()
    r = client.post("/api/v1/tutor/chat", json={"message": "hi"}, headers=h)
    ev = core(events(r.text))
    assert ev[-1][0] == "error" and ev[-1][1]["partial"] is True and "hunter2" not in r.text
    cid = ev[0][1]["conversation_id"]
    msgs = client.get(f"/api/v1/tutor/conversations/{cid}", headers=h).json()["messages"]
    assert [(m["role"], m["status"], m["content"]) for m in msgs] == [("user", "complete", "hi"), ("assistant", "failed", "a")]   # kept, never shown as a finished answer


def test_tutor_validation_and_auth(client, auth, fake):
    assert client.post("/api/v1/tutor/chat", json={"message": "x"}).status_code == 401
    assert client.post("/api/v1/tutor/chat", json={"message": ""}, headers=auth()).status_code == 422
    assert client.post("/api/v1/tutor/chat", json={"message": "x", "language": "fr"}, headers=auth()).status_code == 422


def test_narrate_sends_only_aggregates(client, auth, fake):
    r = client.post("/api/v1/analytics/insights/narrate", headers=auth("secret-user-id-123")).json()
    assert r["text"] == "narrated" and "secret-user-id-123" not in fake.prompts[0]



def test_get_assessment_reopens_without_leaking_keys_and_is_owner_scoped(client, auth):
    h = auth("alice")
    a = client.post("/api/v1/assessments", json={"skill": "python", "n_questions": 3}, headers=h).json()
    g = client.get(f"/api/v1/assessments/{a['assessment_id']}", headers=h)
    assert g.status_code == 200 and g.json()["status"] == "open" and g.json()["skill"] == "Python"
    assert [q["question_id"] for q in g.json()["questions"]] == [q["question_id"] for q in a["questions"]]
    leaked = {k for q in g.json()["questions"] for k in q} & {"correct", "correct_options", "explanation", "solution_approach", "test_cases", "accepted_answers", "rubric"}
    assert not leaked
    assert client.get(f"/api/v1/assessments/{a['assessment_id']}", headers=auth("bob")).status_code == 404
    assert client.get("/api/v1/assessments/nope", headers=h).status_code == 404
