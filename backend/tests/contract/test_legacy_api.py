"""Contract tests for the pre-existing /auth and /learning API (must keep working unchanged for the frontend)."""
import pytest
from tests.conftest import make_token

QUESTIONS = [{"question": f"Q{i}", "options": ["a", "b", "c", "d"], "correct_answer": "b"} for i in range(10)]


class FakeTable:
    def __init__(self, db, name): self.db, self.name, self.f, self._ins = db, name, [], None
    def select(self, *_): return self
    def eq(self, k, v): self.f.append((k, v)); return self
    def order(self, *a, **k): return self
    def limit(self, n): return self
    def insert(self, row): self._ins = row; return self
    def execute(self):
        rows = self.db.setdefault(self.name, [])
        if self._ins is not None:
            row = {"id": f"id-{len(rows) + 1}", **self._ins}; rows.append(row)
            class R: data = [row]
            return R()
        class R: pass
        R.data = [r for r in rows if all(r.get(k) == v for k, v in self.f)]
        return R()


class FakeDB:
    def __init__(self): self.d = {}
    def table(self, name): return FakeTable(self.d, name)


@pytest.fixture
def legacy(client, monkeypatch):
    db = FakeDB()
    import routes.auth, services.content_service as cs
    monkeypatch.setattr(routes.auth, "get_supabase", lambda: db)
    monkeypatch.setattr(cs, "get_supabase", lambda: db)
    return client, db


def test_register_login_roundtrip(legacy):
    c, _ = legacy
    r = c.post("/auth/register", json={"name": "Asha K", "email": "Asha@Example.com", "password": "pässwörd-1234"})
    assert r.status_code == 201 and r.json()["user"]["email"] == "asha@example.com" and r.json()["token_type"] == "bearer"
    assert c.post("/auth/register", json={"name": "Asha K", "email": "asha@example.com", "password": "x" * 9}).status_code == 409
    ok = c.post("/auth/login", json={"email": "asha@example.com", "password": "pässwörd-1234"})
    assert ok.status_code == 200 and ok.json()["access_token"]
    assert c.post("/auth/login", json={"email": "asha@example.com", "password": "wrong-password"}).status_code == 401
    assert c.post("/auth/login", json={"email": "nobody@example.com", "password": "x"}).json()["detail"] == "Invalid email or password."


def test_long_multibyte_password_roundtrip(legacy):
    c, _ = legacy
    pw = "é" * 60  # 120 bytes
    assert c.post("/auth/register", json={"name": "Zed Z", "email": "z@example.com", "password": pw}).status_code == 201
    assert c.post("/auth/login", json={"email": "z@example.com", "password": pw}).status_code == 200


def test_learning_endpoints_require_auth(client):
    for m, p in [("post", "/learning/generate-quiz"), ("post", "/learning/submit-quiz"), ("post", "/learning/generate-content"),
                 ("get", "/learning/history"), ("post", "/learning/agent-chat")]:
        assert client.request(m.upper(), p).status_code in (401, 403), p


def test_quiz_generate_returns_contract_plus_token_and_grades_from_server_copy(legacy, monkeypatch):
    c, _ = legacy
    import services.quiz_service as qs
    async def fake(topic): return list(QUESTIONS)
    monkeypatch.setattr(qs, "gemini_generate_quiz", fake)
    h = {"Authorization": f"Bearer {make_token('u1')}"}
    g = c.post("/learning/generate-quiz", json={"topic": "Python"}, headers=h)
    assert g.status_code == 200
    body = g.json()
    assert set(body) >= {"topic", "questions"} and body["quiz_token"]
    # attacker rewrites the client-side correct answers to "a" for everything and answers "a"
    forged = [{**q, "correct_answer": "a"} for q in body["questions"]]
    r = c.post("/learning/submit-quiz", json={"topic": "Python", "answers": {str(i): 0 for i in range(10)},
                                              "questions": forged, "quiz_token": body["quiz_token"]}, headers=h)
    assert r.status_code == 200 and r.json()["score"] == 0 and r.json()["level"] == "Beginner"
    honest = c.post("/learning/submit-quiz", json={"topic": "Python", "answers": {str(i): 1 for i in range(10)},
                                                   "questions": [], "quiz_token": body["quiz_token"]}, headers=h).json()
    assert honest["score"] == 10 and honest["level"] == "Expert" and set(honest) == {"topic", "score", "total", "level", "breakdown"}


def test_quiz_token_is_user_bound_and_tamper_proof(legacy, monkeypatch):
    c, _ = legacy
    import services.quiz_service as qs
    async def fake(topic): return list(QUESTIONS)
    monkeypatch.setattr(qs, "gemini_generate_quiz", fake)
    tok = c.post("/learning/generate-quiz", json={"topic": "Python"}, headers={"Authorization": f"Bearer {make_token('u1')}"}).json()["quiz_token"]
    other = {"Authorization": f"Bearer {make_token('u2')}"}
    assert c.post("/learning/submit-quiz", json={"topic": "Python", "answers": {}, "quiz_token": tok}, headers=other).status_code == 403
    assert c.post("/learning/submit-quiz", json={"topic": "Python", "answers": {}, "quiz_token": tok + "x"},
                  headers={"Authorization": f"Bearer {make_token('u1')}"}).status_code == 400


def test_legacy_unsigned_submit_still_works_by_default_and_can_be_disabled(legacy, monkeypatch):
    c, _ = legacy
    h = {"Authorization": f"Bearer {make_token('u1')}"}
    body = {"topic": "Python", "answers": {"0": 1}, "questions": QUESTIONS[:1]}
    assert c.post("/learning/submit-quiz", json=body, headers=h).json()["score"] == 1
    from config import settings
    monkeypatch.setattr(settings, "ALLOW_UNSIGNED_QUIZ", False)
    assert c.post("/learning/submit-quiz", json=body, headers=h).status_code == 400


def test_ai_errors_do_not_leak_internals(legacy, monkeypatch):
    c, _ = legacy
    import services.quiz_service as qs
    async def boom(topic): raise RuntimeError("API key AIza-SECRET invalid")
    monkeypatch.setattr(qs, "gemini_generate_quiz", boom)
    r = c.post("/learning/generate-quiz", json={"topic": "Python"}, headers={"Authorization": f"Bearer {make_token('u1')}"})
    assert r.status_code == 503 and "AIza" not in r.text


def test_history_scoped_to_user(legacy):
    c, db = legacy
    db.d["learning_history"] = [{"id": "1", "user_id": "u1", "topic": "A", "score": 5, "level": "Intermediate", "created_at": "t"},
                                {"id": "2", "user_id": "u2", "topic": "B", "score": 9, "level": "Expert", "created_at": "t"}]
    r = c.get("/learning/history", headers={"Authorization": f"Bearer {make_token('u1')}"}).json()
    assert [h["topic"] for h in r["history"]] == ["A"]
