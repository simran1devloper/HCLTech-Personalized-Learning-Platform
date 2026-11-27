import json

import pytest

from services import content_generation as cg
from tests.conftest import make_token
from tests.unit.test_content_generation import good_output


class FakeModel:
    def __init__(self, fail=()):
        self.fail, self.calls, self.override = set(fail), [], None

    async def __call__(self, sec, prompt):
        self.calls.append(sec.name)
        if sec.name in self.fail or "*" in self.fail:
            raise RuntimeError("model error")
        return (self.override if self.override and sec.kind == "markdown" else good_output(sec)), False


@pytest.fixture
def model(monkeypatch):
    m = FakeModel()
    monkeypatch.setattr(cg, "_call_model", m)
    monkeypatch.setattr(cg, "cache_key", lambda t, l, s: f"v1:study-content:1:shared:{t.lower()}:{l}:{s}")
    return m


H = lambda uid="u1": {"Authorization": f"Bearer {make_token(uid)}"}
BODY = {"topic": "Python decorators", "level": "Intermediate", "score": 6}
FIELDS = {"topic", "level", "summary", "key_concepts", "deep_dive", "real_world_applications", "viva_voice",
          "best_practices", "practical_guide", "learning_roadmap", "flashcards", "recommendations", "agent_intro", "incomplete_sections"}


def sse(text):
    out = []
    for block in text.strip().split("\n\n"):
        ev, data = block.split("\n", 1)
        out.append((ev[7:], json.loads(data[6:])))
    return out


def real_user(client, email):
    tok = client.post("/auth/register", json={"name": "Real User", "email": email, "password": "password-123"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_legacy_response_keeps_best_practices_as_an_alias_of_the_new_guide(client, model):
    d = client.post("/learning/generate-content", json=BODY, headers=H()).json()
    assert d["practical_guide"] and d["best_practices"] == d["practical_guide"]
    assert "practical_guide" in cg.SECTION_NAMES and "best_practices" not in cg.SECTION_NAMES


def test_full_generation_contract_cache_and_history(client, model):
    a, b = real_user(client, "a@example.com"), real_user(client, "b@example.com")
    r = client.post("/learning/generate-content", json=BODY, headers=a)
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == FIELDS and d["incomplete_sections"] == [] and len(d["flashcards"]) == 8 and d["summary"].startswith("## Heading")
    assert sorted(model.calls) == sorted(cg.SECTION_NAMES)
    n = len(model.calls)
    again = client.post("/learning/generate-content", json=BODY, headers=b).json()   # identical request -> cache
    assert len(model.calls) == n and again["summary"] == d["summary"]
    assert [h["topic"] for h in client.get("/learning/history", headers=a).json()["history"]] == ["Python decorators"]
    assert [h["topic"] for h in client.get("/learning/history", headers=b).json()["history"]] == ["Python decorators"]


def test_partial_failure_returns_everything_else_and_is_not_cached(client, model):
    model.fail = {"flashcards"}
    d = client.post("/learning/generate-content", json=BODY, headers=H()).json()
    assert d["incomplete_sections"] == ["flashcards"] and d["flashcards"] == [] and "## Heading" in d["deep_dive"]
    assert model.calls.count("flashcards") == 2  # one retry for the failing section only
    model.fail = set(); before = len(model.calls)
    d2 = client.post("/learning/generate-content", json=BODY, headers=H()).json()
    assert d2["incomplete_sections"] == [] and len(model.calls) > before


def test_total_failure_is_503_without_internals(client, model):
    model.fail = {"*"}
    r = client.post("/learning/generate-content", json=BODY, headers=H())
    assert r.status_code == 503 and "model error" not in r.text


def test_stream_emits_each_section_then_full_content(client, model):
    r = client.post("/learning/generate-content/stream", json=BODY, headers=H())
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = sse(r.text)
    meta = ev[0][1]
    assert ev[0][0] == "meta" and meta["sections"] == cg.SECTION_NAMES and meta["total"] == 10
    assert [x["id"] for x in meta["stages"]] == ["plan", *[f"section:{n}" for n in cg.SECTION_NAMES], "review", "save"]
    assert all(x["status"] == "pending" for x in meta["stages"])
    sections = [d for e, d in ev if e == "section"]
    assert sorted(d["name"] for d in sections) == sorted(cg.SECTION_NAMES) and all(d["ok"] for d in sections)
    # the streaming endpoint is new: it exposes practical_guide only (best_practices remains on the legacy non-streaming response)
    assert ev[-1][0] == "done" and set(ev[-1][1]["content"]) == FIELDS - {"best_practices"}
    cached = sse(client.post("/learning/generate-content/stream", json=BODY, headers=H()).text)  # second time: cache
    assert cached[-1][1]["cached"] is True and len([e for e, _ in cached if e == "section"]) == 10
    assert any(e == "stage" and d["id"] == "plan" and "saved copy" in d["detail"] for e, d in cached)


def test_stream_regenerates_only_failed_sections(client, model):
    model.fail = {"viva_voice"}
    ev = sse(client.post("/learning/generate-content/stream", json=BODY, headers=H()).text)
    assert ev[-1][1]["incomplete_sections"] == ["viva_voice"]
    model.fail = set(); model.calls.clear()
    ev = sse(client.post("/learning/generate-content/stream", json={**BODY, "sections": ["viva_voice"]}, headers=H()).text)
    assert model.calls == ["viva_voice"] and [d["name"] for e, d in ev if e == "section"] == ["viva_voice"]
    assert ev[-1][0] == "done" and ev[-1][1]["incomplete_sections"] == [] and ev[-1][1]["content"] is None


def test_stream_validation_and_auth(client, model):
    assert client.post("/learning/generate-content/stream", json=BODY).status_code in (401, 403)
    assert client.post("/learning/generate-content/stream", json={**BODY, "sections": ["evil"]}, headers=H()).status_code == 422
    assert client.post("/learning/generate-content/stream", json={**BODY, "sections": []}, headers=H()).status_code == 422
