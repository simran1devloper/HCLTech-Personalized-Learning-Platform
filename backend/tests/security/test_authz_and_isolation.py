import time
from jose import jwt
from tests.conftest import make_token


def test_cross_user_assessment_and_roadmap_isolation(client, auth):
    a, b = auth("alice"), auth("bob")
    asm = client.post("/api/v1/assessments", json={"skill": "python", "n_questions": 2}, headers=a).json()
    for path, method in [(f"/api/v1/assessments/{asm['assessment_id']}/results", "get"),
                         (f"/api/v1/assessments/{asm['assessment_id']}/submit", "post")]:
        kw = {"json": {"answers": {}}} if method == "post" else {}
        r = getattr(client, method)(path, headers=b, **kw)
        assert r.status_code == 404, path  # existence not revealed
    rm = client.post("/api/v1/roadmaps/generate", json={"role_slug": "ai-engineer"}, headers=a).json()
    assert client.get(f"/api/v1/roadmaps/{rm['roadmap_id']}", headers=b).status_code == 404
    assert client.post(f"/api/v1/roadmaps/{rm['roadmap_id']}/replan", headers=b).status_code == 404
    assert client.post(f"/api/v1/roadmaps/{rm['roadmap_id']}/milestones/{rm['milestones'][0]['item_id']}/complete", headers=b).status_code == 404


def test_cross_user_proficiency_and_goals_isolated(client, auth):
    a, b = auth("alice"), auth("bob")
    asm = client.post("/api/v1/assessments", json={"skill": "python", "n_questions": 2}, headers=a).json()
    client.post(f"/api/v1/assessments/{asm['assessment_id']}/submit", json={"answers": {q["question_id"]: 0 for q in asm["questions"]}}, headers=a)
    client.post("/api/v1/goals", json={"role_slug": "ai-engineer"}, headers=a)
    assert client.get("/api/v1/proficiency", headers=a).json()["items"]
    assert client.get("/api/v1/proficiency", headers=b).json()["items"] == []
    assert client.get("/api/v1/goals", headers=b).json() == []


def test_jwt_attacks_rejected(client):
    sec = "test-secret-key-test-secret-key-0123456789"
    bad = {
        "wrong secret": make_token("u", secret="x" * 40),
        "expired": make_token("u", exp_delta=-10),
        "none alg": "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJ1In0.",
        "no sub": jwt.encode({"exp": int(time.time()) + 100}, sec, algorithm="HS256"),
        "wrong alg": jwt.encode({"sub": "u", "exp": int(time.time()) + 100}, sec, algorithm="HS512"),
        "garbage": "not.a.jwt",
    }
    for name, tok in bad.items():
        r = client.get("/api/v1/proficiency", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401, name


def test_rate_limit(client, auth, monkeypatch):
    from app.core import security
    security._limiter = security.SlidingWindowLimiter(3)
    h = auth()
    codes = [client.get("/api/v1/proficiency", headers=h).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    r = client.get("/api/v1/proficiency", headers=h)
    assert r.json()["error"]["code"] == "rate_limited" and "retry-after" in r.headers
    assert client.get("/api/v1/proficiency", headers=auth("other")).status_code == 200  # per-user


def test_internal_errors_do_not_leak(client, auth, monkeypatch):
    from app.core import container
    def boom(*a, **k): raise RuntimeError("secret db password=hunter2")
    monkeypatch.setattr(container.LearnerStore, "estimates", boom)
    r = client.get("/api/v1/proficiency", headers=auth())
    assert r.status_code == 500 and "hunter2" not in r.text and r.json()["error"]["code"] == "internal_error"


def test_injection_in_inputs_is_inert(client, auth):
    h = auth()
    r = client.post("/api/v1/skills/discover", json={"names": ["'; DROP TABLE skills;--", "<script>alert(1)</script>"]}, headers=h)
    assert r.status_code == 200
    assert client.get("/api/v1/skills?limit=5", headers=h).json()["total"] >= 20
