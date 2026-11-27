"""Skill Explorer over HTTP against the real container (SQLite in memory): graph, detail, search, evidence, careers, cycles."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.container import get_container
from app.domains.learning_intelligence.proficiency import Evidence, EvidenceKind as K
from tests.conftest import make_token
from tests.integration.test_assessment_flow_api import A, answer_all, start

H = lambda u="explorer-1": {"Authorization": f"Bearer {make_token(u)}"}  # noqa: E731
ROLES = ["ai-engineer", "data-engineer", "python-backend-engineer", "full-stack-developer", "devops-engineer", "cloud-engineer", "cybersecurity-engineer"]
NOW = datetime.now(timezone.utc)


def sid(slug):
    return get_container().registry.resolve(slug, 1.0).skill_id


def give(user, slug, *, score=.9, diff=4.0, n=10, days=2, k=2, kind=K.QUIZ):
    get_container().store.add_evidence(user, [Evidence(skill_id=sid(slug), kind=kind, score=score, difficulty=diff, n_items=n, occurred_at=NOW - timedelta(days=days + i),
                                                       source_id=f"t-{slug}-{kind.value}-{i}-{days}") for i in range(k)])


def graph(client, role="ai-engineer", user="explorer-1"):
    r = client.get(f"/api/v1/careers/{role}/skill-graph", headers=H(user))
    assert r.status_code == 200, r.text
    return r.json()


def by_slug(g):
    return {n["slug"]: n for n in g["nodes"]}


# ------------------------------------------------------------------ graph generation
@pytest.mark.parametrize("role", ROLES)
def test_graph_is_generated_from_data_for_every_supported_career(client, role):
    g = graph(client, role)
    ids = {n["skill_id"] for n in g["nodes"]}
    c = get_container()
    r = c.careers.get_role(role)
    assert {t.skill_id for t in c.careers.targets_for(r)} <= ids                       # every career requirement is a node
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])         # no dangling edges
    for n in g["nodes"]:                                                               # the scope is closed under prerequisites
        assert set(n["prerequisites"]) <= ids
    assert g["metrics"]["skills_total"] == len(g["nodes"]) and g["role"]["slug"] == role
    assert all(n["status"] in ("ready", "not_started") for n in g["nodes"])           # a new learner has no evidence
    assert g["metrics"]["readiness"] == 0 and g["metrics"]["coverage"] == 0


def test_edges_match_the_registry_dependencies_not_a_hardcoded_list(client):
    g = graph(client, "ai-engineer")
    c = get_container()
    ids = {n["skill_id"] for n in g["nodes"]}
    expected = {(d.prerequisite_id, d.skill_id) for d in c.registry.repo.list_dependencies() if d.skill_id in ids and d.prerequisite_id in ids}
    assert {(e["source"], e["target"]) for e in g["edges"]} == expected and expected
    c.registry.add_dependency(sid("prompt-engineering"), sid("sql"), 1.0)
    c.explorer.invalidate()
    assert ("SQL", "Prompt Engineering") in {(next(n["name"] for n in graph(client, "ai-engineer")["nodes"] if n["skill_id"] == e["source"]),
                                              next(n["name"] for n in graph(client, "ai-engineer")["nodes"] if n["skill_id"] == e["target"])) for e in graph(client, "ai-engineer")["edges"]}


def test_unknown_role_and_auth(client):
    assert client.get("/api/v1/careers/nope/skill-graph", headers=H()).status_code == 404
    assert client.get("/api/v1/careers/ai-engineer/skill-graph").status_code == 401


# ------------------------------------------------------------------ mastery integration
def test_assessment_submission_flows_into_the_graph(client):
    u = "explorer-assess"
    h = {"Authorization": f"Bearer {make_token(u)}"}
    before = by_slug(graph(client, user=u))["python"]
    assert before["evidence_count"] == 0
    for _ in range(2):  # two graded attempts -> confidence high enough to validate
        a = client.post(A, json={"skill": "python", "n_questions": 10}, headers=h).json()
        r = client.post(f"{A}/{a['assessment_id']}/submit", json={"answers": answer_all(client, a["assessment_id"], user=u)}, headers=h)
        assert r.status_code == 200, r.text
    after = by_slug(graph(client, user=u))["python"]
    assert after["evidence_count"] >= 2 and after["proficiency"] > 0 and after["mastery"] > 0
    g = graph(client, user=u)
    assert g["metrics"]["coverage"] > 0 and g["metrics"]["readiness"] > 0


def test_mastered_blocked_in_progress_ready_not_started_and_revision_states(client):
    u = "explorer-states"
    give(u, "python", score=.95, diff=4.5)                      # mastered (target 4)
    give(u, "sql", score=.2, diff=3, k=1)                       # started but weak
    give(u, "machine-learning", score=.3, diff=3, k=1)          # started, prerequisite python met -> in progress
    give(u, "llm-fundamentals", score=.9, diff=4, k=2, days=300)  # stale -> needs revision
    give(u, "rag", score=.5, diff=3, k=1)                       # started RAG while its prerequisites are missing -> blocked
    n = by_slug(graph(client, "ai-engineer", u))
    assert n["python"]["status"] == "mastered"
    assert n["machine-learning"]["status"] == "in_progress"
    assert n["llm-fundamentals"]["status"] == "needs_revision" and n["llm-fundamentals"]["revision_reason"] == "stale"
    assert n["rag"]["status"] == "blocked" and n["rag"]["missing_prerequisites"]
    assert n["tool-calling"]["status"] == "not_started" and n["tool-calling"]["missing_prerequisites"]
    assert n["sql"]["status"] == "in_progress"
    assert n["python"]["missing_prerequisites"] == []


def test_learning_progress_is_reported_but_never_counts_as_mastery(client):
    u = "explorer-learn"
    c = get_container()
    s = c.history.create_session(u, "FastAPI", "beginner", [("overview", "Overview"), ("core", "Core"), ("practice", "Practice"), ("quiz", "Quiz")])
    with c.history.db.begin() as cx:
        cx.execute(text("update content_sessions set completed_sections=3 where id=:i"), {"i": s["id"]})
    n = by_slug(graph(client, "ai-engineer", u))["fastapi"]
    assert n["learning_progress"] == 0.75 and n["mastery"] == 0 and n["evidence_count"] == 0
    assert n["status"] in ("in_progress", "blocked")
    assert graph(client, "ai-engineer", u)["metrics"]["readiness"] == 0
    d = client.get(f"/api/v1/skills/{n['skill_id']}/detail?role=ai-engineer", headers=H(u)).json()
    assert d["learning_history"][0]["completed_sections"] == 3


def test_shared_skill_evidence_is_preserved_when_switching_careers(client):
    u = "explorer-switch"
    give(u, "python", score=.95, diff=4.5)
    give(u, "docker", score=.9, diff=4)
    a, d = by_slug(graph(client, "ai-engineer", u)), by_slug(graph(client, "devops-engineer", u))
    for slug in ("python", "docker"):
        assert a[slug]["proficiency"] == d[slug]["proficiency"] and a[slug]["confidence"] == d[slug]["confidence"] and a[slug]["evidence_count"] == d[slug]["evidence_count"]
    assert set(a) != set(d)                                                      # the career decides the skill set
    assert a["python"]["target"] != d["python"]["target"]                        # ...and the target level (4 vs 3)
    assert graph(client, "ai-engineer", u)["metrics"]["readiness"] != graph(client, "devops-engineer", u)["metrics"]["readiness"]


def test_readiness_matches_the_legacy_endpoint_and_is_separate_from_coverage(client):
    u = "explorer-ready"
    give(u, "python", score=.9, diff=4)
    give(u, "fastapi", score=.5, diff=3, k=1)
    g = graph(client, "ai-engineer", u)
    legacy = client.get("/api/v1/careers/ai-engineer/readiness", headers=H(u)).json()
    assert legacy["readiness"] == g["metrics"]["readiness"]
    m = g["metrics"]
    assert m["coverage"] == round(m["skills_covered"] / m["skills_total"], 3) and m["readiness"] != m["coverage"]
    b = client.get("/api/v1/careers/ai-engineer/readiness-breakdown", headers=H(u)).json()
    assert b["metrics"] == m


def test_users_are_isolated(client):
    give("explorer-a", "python")
    assert by_slug(graph(client, user="explorer-b"))["python"]["evidence_count"] == 0


# ------------------------------------------------------------------ recommendations
def test_recommendations_are_deterministic_and_explained(client):
    u = "explorer-rec"
    give(u, "python", score=.95, diff=4.5)
    r1 = client.get("/api/v1/careers/ai-engineer/recommendations?limit=5", headers=H(u)).json()
    r2 = client.get("/api/v1/careers/ai-engineer/recommendations?limit=5", headers=H(u)).json()
    assert r1 == r2 and len(r1) == 5
    assert [x["rank"] for x in r1] == [1, 2, 3, 4, 5] and all(x["explanation"] and x["priority"] in ("high", "medium", "low") for x in r1)
    assert sid("python") not in [x["skill_id"] for x in r1]                         # mastered
    assert all((x["suggested_action"] == "learn_prerequisites") == bool(x["missing_prerequisites"]) for x in r1)
    assert client.get("/api/v1/careers/ai-engineer/recommendations?limit=0", headers=H(u)).status_code == 422
    assert graph(client, "ai-engineer", u)["recommendations"][:5] == r1[:5]


# ------------------------------------------------------------------ detail / prerequisites / dependents / search
def test_skill_detail_has_prerequisites_unlocks_evidence_and_related(client):
    u = "explorer-detail"
    give(u, "python", score=.95, diff=4.5)
    give(u, "llm-fundamentals", score=.6, diff=3, k=1)
    d = client.get("/api/v1/skills/rag/detail?role=ai-engineer", headers=H(u))
    assert d.status_code == 200, d.text
    d = d.json()
    assert d["skill"]["name"] == "RAG" and d["career"]["slug"] == "ai-engineer"
    assert {p["name"] for p in d["prerequisites"]} == {"LLM Fundamentals", "PostgreSQL"} and all("met" in p and "required_level" in p for p in d["prerequisites"])
    assert "PostgreSQL" in [m["name"] for m in d["missing_prerequisites"]]
    assert d["unlocks"] == []                                                      # nothing in the catalogue requires RAG
    llm = client.get("/api/v1/skills/llm-fundamentals/detail?role=ai-engineer", headers=H(u)).json()
    assert {"RAG", "Prompt Engineering"} <= {x["name"] for x in llm["unlocks"]}
    assert d["related"] and d["skill"]["hours_remaining"] > 0
    ev = client.get("/api/v1/skills/llm-fundamentals/detail", headers=H(u)).json()["evidence"]
    assert len(ev) == 1 and ev[0]["graded"] is True and ev[0]["kind"] == "quiz"
    assert client.get("/api/v1/skills/not-a-skill/detail", headers=H(u)).status_code == 404


def test_detail_for_a_skill_outside_the_selected_career_still_works(client):
    d = client.get("/api/v1/skills/kubernetes/detail?role=ai-engineer", headers=H()).json()
    assert d["skill"]["name"] == "Kubernetes" and d["career"] is None


def test_prerequisite_and_dependent_traversal(client):
    h = H()
    pre = client.get("/api/v1/skills/rag/prerequisites", headers=h).json()
    assert {"LLM Fundamentals", "PostgreSQL", "SQL", "Machine Learning", "Python"} <= {n["name"] for n in pre}
    direct = client.get("/api/v1/skills/rag/prerequisites?transitive=false", headers=h).json()
    assert {n["name"] for n in direct} == {"LLM Fundamentals", "PostgreSQL"}
    down = client.get("/api/v1/skills/llm-fundamentals/dependents", headers=h).json()
    assert {"RAG", "Prompt Engineering", "Tool Calling", "Agentic AI"} <= {n["name"] for n in down}
    assert [n["depth"] for n in pre] == sorted(n["depth"] for n in pre)


def test_search_and_filters(client):
    u = "explorer-search"
    give(u, "python", score=.95, diff=4.5)
    q = lambda s: client.get("/api/v1/skills/search?" + s, headers=H(u))  # noqa: E731
    assert [n["name"] for n in q("q=postgres").json()["items"]] == ["PostgreSQL"]                      # alias match
    cat = q("category=ai&limit=200").json()
    assert cat["total"] >= 8 and {n["category"] for n in cat["items"]} == {"ai"}
    assert [n["name"] for n in q("status=mastered&role=ai-engineer").json()["items"]] == ["Python"]
    ready = {n["name"] for n in q("status=ready&role=ai-engineer").json()["items"]}
    assert "SQL" in ready and "Python" not in ready
    page = q("limit=5&offset=5").json()
    assert len(page["items"]) == 5 and page["offset"] == 5 and page["total"] > 30
    assert q("status=bogus").status_code == 422 and q("limit=999").status_code == 422 and q("q=" + "x" * 200).status_code == 422
    assert q("q=zzzz-no-such-skill").json()["total"] == 0


# ------------------------------------------------------------------ evidence API validation
EV = {"kind": "project", "score": 0.9, "difficulty": 4, "n_items": 3, "source_id": "github.com/me/proj"}


def post_ev(client, skill, body, user="explorer-ev", q=""):
    return client.post(f"/api/v1/skills/{skill}/evidence{q}", json=body, headers=H(user))


@pytest.mark.parametrize("patch", [{"kind": "quiz"}, {"kind": "self_report"}, {"kind": "completed"}, {"score": 1.2}, {"score": -0.1}, {"difficulty": 0},
                                   {"difficulty": 6}, {"n_items": 0}, {"n_items": 99}, {"source_id": ""}, {"source_id": "a b c"}, {"extra": 1},
                                   {"occurred_at": (NOW + timedelta(days=3)).isoformat()}])
def test_evidence_validation_rejects_bad_input(client, patch):
    assert post_ev(client, "python", {**EV, **patch}).status_code == 422


def test_evidence_requires_a_source_and_a_known_skill(client):
    body = {k: v for k, v in EV.items() if k != "source_id"}
    assert post_ev(client, "python", body).status_code == 422
    assert post_ev(client, "nope", EV).status_code == 404
    assert client.post("/api/v1/skills/python/evidence", json=EV).status_code == 401


def test_manual_evidence_updates_the_estimate_but_cannot_validate_mastery(client):
    u = "explorer-manual"
    for i in range(3):
        r = post_ev(client, "python", {**EV, "n_items": 5, "score": 1, "difficulty": 5, "source_id": f"repo-{i}"}, u, "?role=ai-engineer")
        assert r.status_code == 201, r.text
    n = by_slug(graph(client, "ai-engineer", u))["python"]
    assert n["proficiency"] == 5 and n["evidence_count"] == 3 and n["status"] == "in_progress"      # strong, but no graded assessment
    ev = client.get("/api/v1/skills/python/detail", headers=H(u)).json()["evidence"]
    assert all(e["graded"] is False and e["source_id"].startswith("manual:") for e in ev)
    give(u, "python", score=.9, diff=4, k=1)                                                          # a graded attempt validates it
    assert by_slug(graph(client, "ai-engineer", u))["python"]["status"] == "mastered"


def test_posting_the_same_evidence_twice_is_idempotent(client):
    u = "explorer-idem"
    assert post_ev(client, "sql", EV, u).status_code == 201 and post_ev(client, "sql", EV, u).status_code == 201
    assert by_slug(graph(client, "data-engineer", u))["sql"]["evidence_count"] == 1


# ------------------------------------------------------------------ cycle prevention (service + database)
def test_service_rejects_cycles(client):
    c = get_container()
    r = c.registry
    with pytest.raises(Exception) as e:
        r.add_dependency(sid("python"), sid("fastapi"))
    assert "cycle" in str(e.value).lower()
    with pytest.raises(Exception):
        r.add_dependency(sid("python"), sid("python"))


def test_database_rejects_cycles_even_for_raw_writers(client):
    db = get_container().registry.repo.db
    ins = text("insert into skill_dependencies(skill_id, prerequisite_id, min_proficiency) values(:a,:b,2)")
    with pytest.raises(IntegrityError) as e:
        with db.begin() as cx:
            cx.execute(ins, {"a": sid("python"), "b": sid("fastapi")})   # FastAPI already requires Python
    assert "cycle" in str(e.value).lower()
    with pytest.raises(IntegrityError):
        with db.begin() as cx:
            cx.execute(ins, {"a": sid("python"), "b": sid("python")})   # self dependency (CHECK)
    with pytest.raises(IntegrityError):  # an UPDATE that would close a loop is rejected too
        with db.begin() as cx:
            cx.execute(text("update skill_dependencies set prerequisite_id=:p where skill_id=:s and prerequisite_id=:old"),
                       {"p": sid("fastapi"), "s": sid("api-development"), "old": sid("python")})
    with db.begin() as cx:   # a legitimate new edge is accepted
        cx.execute(ins, {"a": sid("redis"), "b": sid("sql")})


def test_graph_endpoint_is_fast_enough_to_call_per_interaction(client):
    import time
    graph(client)  # warm
    t = time.perf_counter()
    for _ in range(10):
        graph(client)
    assert (time.perf_counter() - t) / 10 < 0.5


def test_a_mastered_skill_is_never_reported_as_blocked_by_skipped_prerequisites(client):
    u = "explorer-skip"
    give(u, "docker", score=.95, diff=5)                        # Docker mastered without any Linux evidence
    g = graph(client, "devops-engineer", u)
    d = by_slug(g)["docker"]
    assert d["status"] == "mastered" and d["missing_prerequisites"] == []
    edge = next(e for e in g["edges"] if e["target"] == d["skill_id"] and e["source"] == sid("linux"))
    assert edge["satisfied"] is True                           # no dashed "unmet" edge into a validated skill
    assert by_slug(g)["kubernetes"]["missing_prerequisites"] == []   # Docker (its prerequisite) is mastered


def test_all_skills_graph_has_statuses_but_no_career_metrics(client):
    u = "explorer-all"
    give(u, "python", score=.95, diff=4.5)
    r = client.get("/api/v1/skill-graph", headers=H(u))
    assert r.status_code == 200, r.text
    g = r.json()
    names = {n["name"] for n in g["nodes"]}
    assert g["role"] is None and g["recommendations"] == [] and g["metrics"]["readiness"] == 0
    assert {"Kubernetes", "Python", "RAG", "Git"} <= names and len(g["nodes"]) >= 35
    assert by_slug(g)["python"]["evidence_count"] >= 1 and g["metrics"]["skills_covered"] == 1
    assert client.get("/api/v1/skill-graph").status_code == 401
