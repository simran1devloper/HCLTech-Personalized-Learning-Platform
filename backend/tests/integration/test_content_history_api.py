"""Content history end to end: persistence, reopen without LLM, continue/resume/regenerate, interruption, concurrency, ownership."""
import asyncio

import pytest

from app.core.container import get_container, reset_container
from services import content_generation as cg
from services import session_generation as sg
from tests.integration.test_content_endpoints import BODY, FakeModel, H, sse
from tests.unit.test_content_generation import good_output

API = "/api/v1/content-sessions"


@pytest.fixture
def model(monkeypatch):
    m = FakeModel()
    monkeypatch.setattr(cg, "_call_model", m)
    monkeypatch.setattr(cg, "cache_key", lambda t, l, s: f"k:{t.lower()}:{l}:{s}")
    return m


def generate(client, headers=None, **extra):
    r = client.post("/learning/generate-content/stream", json={**BODY, **extra}, headers=headers or H())
    assert r.status_code == 200, r.text
    return sse(r.text)


def sid_of(events):
    return events[0][1]["session_id"]


def test_generation_is_persisted_per_section_and_reopening_makes_no_model_calls(client, model):
    ev = generate(client)
    sid = sid_of(ev)
    assert ev[0][1]["session_created"] is True and len(model.calls) == 10
    n = len(model.calls)

    lst = client.get(API, headers=H()).json()
    assert lst["total"] == 1 and lst["items"][0]["id"] == sid
    item = lst["items"][0]
    assert (item["title"], item["learning_level"], item["completed_sections"], item["total_sections"], item["status"]) == ("Python decorators", "Intermediate", 10, 10, "completed")
    assert "content" not in item and "metadata" not in item                      # list is summary-only
    assert client.get(f"{API}/search", params={"q": "decorators"}, headers=H()).json()["total"] == 1
    assert client.get(f"{API}/search", params={"q": "nomatch"}, headers=H()).json()["total"] == 0

    detail = client.get(f"{API}/{sid}", headers=H()).json()
    assert [s["section_key"] for s in detail["sections"]] == cg.SECTION_NAMES and all(s["status"] == "COMPLETED" for s in detail["sections"])
    assert detail["last_accessed_at"] is not None and "content" not in detail["sections"][0]
    content = client.get(f"{API}/{sid}/content", headers=H()).json()
    assert set(content["sections"]) == set(cg.SECTION_NAMES) and content["incomplete_sections"] == []
    assert client.get(f"{API}/{sid}/sections/deep_dive", headers=H()).json()["content"].startswith("## Heading")
    assert len(client.get(f"{API}/{sid}/sections/deep_dive/versions", headers=H()).json()["items"]) == 1
    assert len(model.calls) == n                                                  # none of the reads called the model

    again = generate(client)                                                      # reload / reconnect: same session, no work
    assert sid_of(again) == sid and again[0][1]["session_created"] is False and again[-1][1]["cached"] is True
    assert len(model.calls) == n and client.get(API, headers=H()).json()["total"] == 1


def test_explicit_new_session_is_separate_and_both_are_kept(client, model):
    a = sid_of(generate(client))
    b = sid_of(generate(client, new_session=True))
    assert a != b and client.get(API, headers=H()).json()["total"] == 2


def test_missing_section_is_generated_alone_and_attached_to_the_same_session(client, model):
    model.fail = {"viva_voice"}
    ev = generate(client)
    sid = sid_of(ev)
    assert ev[-1][1]["incomplete_sections"] == ["viva_voice"]
    s = client.get(f"{API}/{sid}", headers=H()).json()
    assert s["completed_sections"] == 9 and s["status"] == "active"
    assert next(x for x in s["sections"] if x["section_key"] == "viva_voice")["status"] == "FAILED"
    assert client.get(f"{API}/{sid}/content", headers=H()).json()["incomplete_sections"] == ["viva_voice"]   # placeholder text was NOT saved

    model.fail = set(); model.calls.clear()
    ev = sse(client.post(f"{API}/{sid}/sections/viva_voice/generate", headers=H()).text)
    assert model.calls == ["viva_voice"] and ev[-1][0] == "done"
    s = client.get(f"{API}/{sid}", headers=H()).json()
    assert s["completed_sections"] == 10 and s["status"] == "completed"
    model.calls.clear()                                                           # generating a completed section = database read
    sse(client.post(f"{API}/{sid}/sections/viva_voice/generate", headers=H()).text)
    assert model.calls == []


def test_continue_resumes_only_what_is_missing(client, model):
    model.fail = {"flashcards", "recommendations"}
    sid = sid_of(generate(client))
    model.fail = set(); model.calls.clear()
    ev = sse(client.post(f"{API}/{sid}/continue", headers=H()).text)
    assert sorted(model.calls) == ["flashcards", "recommendations"]
    assert ev[0][1]["reused"] and len(ev[0][1]["reused"]) == 8 and client.get(f"{API}/{sid}", headers=H()).json()["status"] == "completed"


def test_regenerate_keeps_previous_version_and_failed_regeneration_changes_nothing(client, model):
    sid = sid_of(generate(client))
    before = client.get(f"{API}/{sid}/sections/summary", headers=H()).json()
    model.fail = {"summary"}; model.calls.clear()
    ev = sse(client.post(f"{API}/{sid}/sections/summary/regenerate", headers=H()).text)
    assert not [d for e, d in ev if e == "section" and d["ok"]]
    after_fail = client.get(f"{API}/{sid}/sections/summary", headers=H()).json()
    assert after_fail["content"] == before["content"] and after_fail["status"] == "COMPLETED" and after_fail["content_version"] == 1
    assert len(client.get(f"{API}/{sid}/sections/summary/versions", headers=H()).json()["items"]) == 1

    model.fail = set(); model.calls.clear()
    monkey_content = "## Regenerated\n\nA new and sufficiently long paragraph about the topic, clearly complete."
    model.override = monkey_content
    sse(client.post(f"{API}/{sid}/sections/summary/regenerate", headers=H()).text)
    assert model.calls == ["summary"]                                              # only that section
    vs = client.get(f"{API}/{sid}/sections/summary/versions", params={"include_content": "true"}, headers=H()).json()["items"]
    assert [v["version"] for v in vs] == [2, 1] and [v["active"] for v in vs] == [True, False] and vs[1]["content"] == before["content"]
    assert client.get(f"{API}/{sid}/sections/summary", headers=H()).json()["content_version"] == 2
    assert client.post(f"{API}/{sid}/sections/summary/versions/1/activate", headers=H()).json()["content_version"] == 1
    assert client.get(f"{API}/{sid}/sections/summary", headers=H()).json()["content"] == before["content"]
    assert client.get(f"{API}/{sid}", headers=H()).json()["completed_sections"] == 10


def test_followup_request_adds_a_section_without_touching_the_guide(client, model):
    sid = sid_of(generate(client))
    snap = client.get(f"{API}/{sid}/content", headers=H()).json()["sections"]
    model.calls.clear()
    ev = sse(client.post(f"{API}/{sid}/continue", json={"instruction": "Explain idempotency with a FastAPI example"}, headers=H()).text)
    assert ev[-1][0] == "done" and len(model.calls) == 1
    s = client.get(f"{API}/{sid}", headers=H()).json()
    extras = [x for x in s["sections"] if x["section_key"].startswith("extra-")]
    assert len(extras) == 1 and extras[0]["section_title"].startswith("Explain idempotency") and extras[0]["status"] == "COMPLETED"
    assert s["total_sections"] == 10 and s["completed_sections"] == 10 and s["status"] == "completed"   # progress unaffected
    content = client.get(f"{API}/{sid}/content", headers=H()).json()["sections"]
    assert {k: v for k, v in content.items() if not k.startswith("extra-")} == snap


def test_session_management_rename_tags_archive_duplicate_delete(client, model):
    sid = sid_of(generate(client))
    r = client.patch(f"{API}/{sid}", json={"title": "My decorators notes", "tags": ["Python", "Backend"]}, headers=H()).json()
    assert r["title"] == "My decorators notes" and r["tags"] == ["backend", "python"] and len(model.calls) == 10
    assert client.get(f"{API}/search", params={"q": "backend"}, headers=H()).json()["total"] == 1
    assert client.post(f"{API}/{sid}/archive", headers=H()).json()["status"] == "archived"
    assert client.get(API, headers=H()).json()["total"] == 0 and client.get(API, params={"status": "archived"}, headers=H()).json()["total"] == 1
    assert client.post(f"{API}/{sid}/restore", headers=H()).json()["status"] == "completed"
    d = client.post(f"{API}/{sid}/duplicate", headers=H())
    assert d.status_code == 201 and d.json()["completed_sections"] == 10 and d.json()["id"] != sid and len(model.calls) == 10
    assert client.delete(f"{API}/{sid}", headers=H()).status_code == 204
    assert client.get(f"{API}/{sid}", headers=H()).status_code == 404 and client.delete(f"{API}/{sid}", headers=H()).status_code == 404
    assert client.get(API, headers=H()).json()["total"] == 1                       # the duplicate is independent


def test_users_cannot_see_or_change_each_others_sessions(client, model):
    sid = sid_of(generate(client, H("alice")))
    bob = H("bob")
    n = len(model.calls)
    probes = [("get", f"{API}/{sid}"), ("get", f"{API}/{sid}/content"), ("get", f"{API}/{sid}/sections"), ("get", f"{API}/{sid}/sections/summary"),
              ("get", f"{API}/{sid}/sections/summary/versions"), ("get", f"{API}/{sid}/events"), ("patch", f"{API}/{sid}"), ("delete", f"{API}/{sid}"),
              ("post", f"{API}/{sid}/archive"), ("post", f"{API}/{sid}/restore"), ("post", f"{API}/{sid}/duplicate"), ("post", f"{API}/{sid}/continue"),
              ("post", f"{API}/{sid}/sections/summary/generate"), ("post", f"{API}/{sid}/sections/summary/regenerate"),
              ("post", f"{API}/{sid}/sections/summary/versions/1/activate"), ("post", f"{API}/{sid}/feedback")]
    for method, path in probes:
        kw = {"json": {"title": "x"}} if method == "patch" else {"json": {"rating": 5}} if path.endswith("feedback") else {}
        r = getattr(client, method)(path, headers=bob, **kw)
        assert r.status_code == 404, (method, path, r.status_code)
    assert client.get(API, headers=bob).json()["total"] == 0 and client.get(f"{API}/search", params={"q": "python"}, headers=bob).json()["total"] == 0
    leak = client.post("/learning/generate-content/stream", json={**BODY, "session_id": sid}, headers=bob)
    assert leak.status_code == 404
    assert len(model.calls) == n
    assert client.get(f"{API}/{sid}", headers=H("alice")).json()["title"] == "Python decorators"        # untouched
    assert client.get(f"{API}/{sid}", headers={}).status_code in (401, 403)


def test_validation(client, model):
    assert client.get(API, params={"sort": "bogus"}, headers=H()).status_code == 422
    assert client.get(API, params={"status": "bogus"}, headers=H()).status_code == 422
    assert client.get(API, params={"limit": 1000}, headers=H()).status_code == 422
    assert client.get(f"{API}/search", headers=H()).status_code == 422
    assert client.post(API, json={"topic": "x"}, headers=H()).status_code == 422
    c = client.post(API, json={"topic": "Rust ownership", "level": "Beginner"}, headers=H())
    assert c.status_code == 201 and c.json()["created"] and c.json()["completed_sections"] == 0 and model.calls == []
    assert client.post(API, json={"topic": "rust  ownership", "level": "Beginner"}, headers=H()).json()["id"] == c.json()["id"]
    sid = c.json()["id"]
    assert client.post(f"{API}/{sid}/sections/not_a_section/generate", headers=H()).status_code == 422
    assert client.post(f"{API}/{sid}/continue", json={"instruction": "x"}, headers=H()).status_code == 422


def test_data_survives_an_api_restart_and_a_second_device(tmp_path, monkeypatch, model):
    from fastapi.testclient import TestClient
    from app.core.config import get_settings
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "app.db"))
    get_settings.cache_clear(); reset_container()
    import main
    try:
        with TestClient(main.app) as c1:
            sid = sid_of(generate(c1))
        reset_container()                                                          # API process "restarts": all in-memory state gone
        model.calls.clear()
        with TestClient(main.app) as c2:                                           # another device, same user (token is stateless)
            assert c2.get(API, headers=H()).json()["items"][0]["id"] == sid
            assert c2.get(f"{API}/{sid}/content", headers=H()).json()["incomplete_sections"] == []
            assert sid_of(generate(c2)) == sid and model.calls == []
    finally:
        reset_container(); get_settings.cache_clear()


# ------------------------------------------------------------------------------------------ async: interruption & concurrency
class SlowModel(FakeModel):
    def __init__(self, delay=0.05, **kw):
        super().__init__(**kw)
        self.delay = delay

    async def __call__(self, sec, prompt):
        self.calls.append(sec.name)
        await asyncio.sleep(self.delay)
        return good_output(sec), False


def _fresh(monkeypatch, m):
    reset_container()
    monkeypatch.setattr(cg, "_call_model", m)
    monkeypatch.setattr(cg, "cache_key", lambda t, l, s: f"k:{t.lower()}:{l}:{s}")
    monkeypatch.setattr(sg, "_last_recovery", 0.0)
    store = get_container().history
    s, _ = sg.open_session(store, "u1", "Async topic", "Intermediate", 5)
    return store, s


def test_interrupted_generation_keeps_finished_sections_and_resume_regenerates_only_the_rest(monkeypatch):
    m = SlowModel(0.03)
    store, s = _fresh(monkeypatch, m)

    async def interrupted():
        gen = sg.stream_session(store, "u1", s)
        done = 0
        async for ev in gen:
            if ev["event"] == "section" and ev["data"]["ok"]:
                done += 1
                if done == 3:
                    break
        await gen.aclose()                                                         # the client went away mid-generation
        return done

    asyncio.run(interrupted())
    got = store.get_session("u1", s["id"])
    assert 3 <= got["completed_sections"] < 10 and got["status"] == "active"       # not stuck as "generating"; finished work kept
    assert not store.jobs_for_session("u1", s["id"], active_only=True)            # leases released immediately
    kept = set(store.get_contents("u1", s["id"]))
    calls_before = list(m.calls)

    async def resume():
        return [ev async for ev in sg.stream_session(store, "u1", s)]

    ev = asyncio.run(resume())
    regenerated = [c for c in m.calls[len(calls_before):]]
    assert not (set(regenerated) & kept)                                           # nothing already saved was generated again
    assert ev[-1]["event"] == "done" and ev[-1]["data"]["incomplete_sections"] == []
    assert store.get_session("u1", s["id"])["status"] == "completed"
    assert any(e["event_type"] == "interrupted" for e in store.session_events("u1", s["id"]))


def test_simultaneous_requests_for_one_session_never_duplicate_generation(monkeypatch):
    m = SlowModel(0.05)
    store, s = _fresh(monkeypatch, m)

    async def one():
        return [ev async for ev in sg.stream_session(store, "u1", s)]

    async def both():
        return await asyncio.gather(one(), one(), one())

    results = asyncio.run(both())
    assert sorted(m.calls) == sorted(cg.SECTION_NAMES)                             # 10 calls total, not 30
    for evs in results:
        assert evs[-1]["event"] == "done" and evs[-1]["data"]["incomplete_sections"] == []
        assert {e["data"]["name"] for e in evs if e["event"] == "section" and e["data"]["ok"]} == set(cg.SECTION_NAMES)
    assert len(store.versions("u1", s["id"], "summary")) == 1


def test_abandoned_worker_is_recovered_on_next_access(client, model):
    sid = sid_of(generate(client))
    store = get_container().history
    from datetime import timedelta
    from app.infrastructure.database import tables as T
    store.save_section("u1", sid, "summary", "x", force_new=True)
    j, _ = store.create_or_get_job(sid, "deep_dive", f"{sid}:deep_dive:v9")
    assert store.claim_job(j["id"], "dead-api-process", lease_s=1, now=T.utcnow() - timedelta(seconds=30))
    assert store.get_session("u1", sid)["status"] == "generating"
    sg._last_recovery = 0.0
    assert client.get(f"{API}/{sid}", headers=H()).json()["status"] == "completed"   # recovered: content was complete all along
    assert store.get_job(j["id"])["status"] == "CANCELLED"


def test_partial_regeneration_does_not_add_a_legacy_history_row(client, model, monkeypatch):
    from services import content_service
    rows = []
    async def rec(user_id, topic, score, level): rows.append(topic)
    monkeypatch.setattr("routes.learning._record_history", rec)
    model.fail = {"viva_voice"}
    generate(client)
    model.fail = set()
    generate(client, sections=["viva_voice"])
    assert rows == ["Python decorators"]
