"""Study workspace backend: career-independent personalization, profile-aware cache, reading state, pipeline report, knowledge checks."""
import pytest

from app.core.container import get_container
from services import content_generation as cg
from services import personalization as pz
from tests.conftest import make_token
from tests.integration.test_content_endpoints import FakeModel, sse
from tests.integration.test_diagnostic_study import D, Gen, key_of, start as start_quiz

CS = "/api/v1/content-sessions"
H = lambda u="learner-1": {"Authorization": f"Bearer {make_token(u)}"}  # noqa: E731
BODY = {"topic": "Python decorators", "level": "Intermediate", "score": 6}
ROLES = ["ai-engineer", "data-engineer", "python-backend-engineer", "full-stack-ai-engineer", "devops-engineer", "cloud-engineer", "cybersecurity-engineer"]


class Recorder(FakeModel):
    def __init__(self):
        super().__init__()
        self.prompts = {}

    async def __call__(self, sec, prompt):
        self.prompts[sec.name] = prompt
        return await super().__call__(sec, prompt)


@pytest.fixture
def model(client, monkeypatch):
    m = Recorder()
    monkeypatch.setattr(cg, "_call_model", m)
    monkeypatch.setattr(cg, "cache_key", lambda t, l, s: f"k:{t.lower()}:{l}:{s}")
    get_container().bank.call = Gen()
    return m


def set_goal(client, user, role):
    return client.post("/api/v1/goals", json={"role_slug": role, "hours_per_week": 5}, headers=H(user))


def finish_diagnostic(client, user="learner-1", fail_concept=True):
    """A completed Python diagnostic where every question of the biggest concept area is answered wrongly. Returns (session_id, weak concept)."""
    from collections import Counter
    sid = start_quiz(client, "Python", 10, user=user, hdr={"X-Idempotency-Key": f"d-{user}"}).json()["session_id"]
    key = key_of(sid)
    weak = Counter(s["concept"] for s in key.values()).most_common(1)[0][0]
    ans = {}
    for qid, s in key.items():
        bad = [j for j in range(len(s["options"])) if j not in s["correct"]][0]
        ans[qid] = (bad if len(s["correct"]) == 1 else [bad]) if (s["concept"] == weak and fail_concept) else (s["correct"][0] if len(s["correct"]) == 1 else s["correct"])
    assert client.post(f"{D}/sessions/{sid}/submit", json={"answers": ans}, headers=H(user)).status_code == 200
    return sid, weak


def generate(client, user="learner-1", **extra):
    r = client.post("/learning/generate-content/stream", json={**BODY, **extra}, headers=H(user))
    assert r.status_code == 200, r.text
    return sse(r.text)


# ------------------------------------------------------------------------------------------------ personalization (pure)
@pytest.mark.parametrize("title", ["AI Engineer", "Data Engineer", "Backend Engineer", "Full Stack Developer", "DevOps Engineer", "Cloud Engineer", "Cybersecurity Engineer"])
def test_the_profile_line_works_for_any_career_and_hardcodes_none(title):
    p = {"career": {"slug": title.lower().replace(" ", "-"), "title": title}, "level": "Intermediate", "weak": ["Joins and subqueries"], "strong": ["Data types"], "diagnostic_session_id": None}
    line = pz.prompt_line(p, "deep_dive")
    assert f"career goal: {title}" in line and "Joins and subqueries" in line and "do not re-teach): Data types" in line and f"relate examples to the {title} role" in line
    assert "Agentic" not in line and "AI Engineer" not in pz.prompt_line({**p, "career": {"slug": "x", "title": "Cloud Engineer"}}, "deep_dive")


def test_depth_budgets_and_fingerprint_follow_the_profile():
    from services.content_generation import SECTIONS
    sec = {s.name: s for s in SECTIONS}
    weak = {"career": None, "level": "Intermediate", "weak": ["A"], "strong": [], "diagnostic_session_id": None}
    expert = {"career": None, "level": "Expert", "weak": [], "strong": ["B"], "diagnostic_session_id": None}
    assert pz.depth_for(weak, "deep_dive") == "deep" and pz.depth_for(weak, "summary") == "standard" and pz.depth_for(expert, "summary") == "brief" and pz.depth_for(None, "deep_dive") == "standard"
    assert pz.scale_section(sec["deep_dive"], weak).max_tokens > sec["deep_dive"].max_tokens and pz.scale_section(sec["summary"], expert).max_tokens < sec["summary"].max_tokens
    assert pz.scale_section(sec["flashcards"], weak).max_tokens == sec["flashcards"].max_tokens                      # JSON sections keep their schema-sized budgets
    assert pz.fingerprint(None) == "" and pz.fingerprint(pz.empty_profile("Intermediate")) == "" and pz.prompt_line(pz.empty_profile(), "deep_dive") == ""
    assert pz.fingerprint(weak) != pz.fingerprint(expert) and pz.fingerprint(weak) == pz.fingerprint({**weak, "level": "Expert"})   # level has its own cache dimension


# ------------------------------------------------------------------------------------------------ personalization end to end
@pytest.mark.parametrize("role,title", [("ai-engineer", "AI Engineer"), ("devops-engineer", "DevOps Engineer"), ("cybersecurity-engineer", "Cybersecurity Engineer"), ("data-engineer", "Data Engineer")])
def test_career_goal_and_diagnostic_results_shape_the_prompts(client, model, role, title):
    set_goal(client, "learner-1", role)
    diag, weak = finish_diagnostic(client)
    ev = generate(client, diagnostic_session_id=diag)
    assert ev[-1][0] == "done"
    deep, summ = model.prompts["deep_dive"], model.prompts["summary"]
    assert f"career goal: {title}" in deep and weak in deep and "Depth for this section: deep" in deep
    assert f"career goal: {title}" in summ and "Depth for this section" not in summ                                      # same profile, standard depth for an overview
    assert all(other not in deep for other in ("AI Engineer", "DevOps Engineer", "Cybersecurity Engineer", "Data Engineer") if other != title)
    sid = ev[0][1]["session_id"]
    prof = client.get(f"{CS}/{sid}", headers=H()).json()["context"]["profile"]
    assert prof["career"]["title"] == title and weak in prof["weak"] and prof["diagnostic_session_id"] == diag           # persisted: the workspace shows it after a refresh


def test_without_a_goal_or_diagnostic_the_prompts_are_unchanged_and_nothing_extra_is_called(client, model):
    ev = generate(client)
    assert ev[-1][0] == "done" and "Learner profile" not in "".join(model.prompts.values())
    assert client.get(f"{CS}/{ev[0][1]['session_id']}", headers=H()).json()["context"]["profile"]["fingerprint"] == ""


def test_a_foreign_or_unfinished_diagnostic_id_is_ignored_not_trusted(client, model):
    diag, weak = finish_diagnostic(client, user="owner")
    generate(client, user="intruder", diagnostic_session_id=diag)
    assert weak not in "".join(model.prompts.values())                                                                   # someone else's results never personalise my guide
    open_id = start_quiz(client, "Python", 10, user="learner-1", hdr={"X-Idempotency-Key": "open"}).json()["session_id"]
    model.prompts.clear()
    generate(client, user="learner-1", level="Beginner", diagnostic_session_id=open_id)
    assert "Learner profile" not in "".join(model.prompts.values())                                                     # an unfinished diagnostic contributes nothing


def test_the_shared_cache_is_never_crossed_between_different_profiles(client, model):
    set_goal(client, "alice", "ai-engineer")
    generate(client, "alice")
    first = len(model.calls)
    assert first == 10
    generate(client, "bob")                                                                                              # no goal: must NOT reuse alice's AI-Engineer-flavoured text
    assert len(model.calls) == first + 10
    set_goal(client, "carol", "ai-engineer")
    generate(client, "carol")                                                                                            # identical profile to alice: pure cache reuse
    assert len(model.calls) == first + 10
    generate(client, "carol", new_session=True)
    assert len(model.calls) == first + 10                                                                                # and a new session of the same profile is served from cache too


# ------------------------------------------------------------------------------------------------ reading state
def a_session(client, user="learner-1"):
    return generate(client, user)[0][1]["session_id"]


def test_reading_state_is_persisted_partial_updates_never_clobber_and_progress_is_computed(client, model):
    sid = a_session(client)
    assert client.get(f"{CS}/{sid}/state", headers=H()).json()["progress"] == {"total": 10, "understood": 0, "opened": 0, "bookmarked": 0, "understood_pct": 0, "reading_pct": 0}
    put = lambda key, **b: client.put(f"{CS}/{sid}/sections/{key}/state", json=b, headers=H())  # noqa: E731
    assert put("summary", opened=True).json()["opened_at"] is not None
    assert put("summary", understood=True).json()["understood"] is True
    r = put("summary", note="remember: decorators wrap functions")
    assert r.json() == {**r.json(), "understood": True, "note": "remember: decorators wrap functions"} and r.json()["opened_at"] is not None          # earlier fields kept
    put("deep_dive", bookmarked=True); put("deep_dive", bookmarked=True)                                                 # idempotent
    st = client.get(f"{CS}/{sid}/state", headers=H()).json()
    assert st["sections"]["deep_dive"]["bookmarked"] is True and st["progress"] == {"total": 10, "understood": 1, "opened": 1, "bookmarked": 1, "understood_pct": 10, "reading_pct": 10}
    assert put("summary", understood=False).json()["understood"] is False and client.get(f"{CS}/{sid}/state", headers=H()).json()["progress"]["understood"] == 0


def test_reading_state_validation_and_ownership(client, model):
    sid = a_session(client)
    put = lambda who, key, **b: client.put(f"{CS}/{sid}/sections/{key}/state", json=b, headers=H(who))  # noqa: E731
    assert put("learner-1", "not_a_section", understood=True).status_code == 404
    assert put("learner-1", "summary", note="x" * 5001).status_code == 422
    assert put("intruder", "summary", understood=True).status_code == 404 and client.get(f"{CS}/{sid}/state", headers=H("intruder")).status_code == 404
    assert client.get(f"{CS}/{sid}/state", headers=H()).json()["sections"] == {}                                         # the intruder wrote nothing
    assert put("learner-1", "summary", note="<script>alert(1)</script>").json()["note"] == "<script>alert(1)</script>"   # stored verbatim; rendered as plain text by the UI


def test_state_survives_an_api_restart(model, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.core.config import get_settings
    from app.core.container import reset_container
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "ws.db")); get_settings.cache_clear(); reset_container()
    import main
    try:
        with TestClient(main.app) as c1:
            get_container().bank.call = Gen()
            sid = generate(c1)[0][1]["session_id"]
            c1.put(f"{CS}/{sid}/sections/summary/state", json={"bookmarked": True, "note": "keep"}, headers=H())
        reset_container()
        with TestClient(main.app) as c2:
            s = c2.get(f"{CS}/{sid}/state", headers=H()).json()["sections"]["summary"]
            assert s["bookmarked"] is True and s["note"] == "keep"
    finally:
        reset_container(); get_settings.cache_clear()


# ------------------------------------------------------------------------------------------------ pipeline report
def test_pipeline_report_distinguishes_generated_from_reused_and_reports_failures(client, model):
    model.fail = {"flashcards"}
    sid = generate(client)[0][1]["session_id"]
    p = client.get(f"{CS}/{sid}/pipeline", headers=H()).json()
    by = {r["section_key"]: r for r in p["sections"]}
    assert [r["section_key"] for r in p["sections"]] == cg.SECTION_NAMES and by["summary"]["status"] == "COMPLETED" and by["summary"]["source"] == "generated"
    assert by["flashcards"]["status"] == "FAILED" and by["flashcards"]["job_status"] == "FAILED" and by["flashcards"]["error_code"] == "generation_failed" and by["flashcards"]["source"] is None
    assert p["totals"]["completed"] == 9 and p["totals"]["generated"] == 9 and p["totals"]["reused"] == 0 and p["totals"]["failed"] == 1
    model.fail = set(); model.calls.clear()
    generate(client, new_session=False)                                                                                  # only the failed section is generated again
    assert model.calls == ["flashcards"]
    p2 = client.get(f"{CS}/{sid}/pipeline", headers=H()).json()
    assert p2["totals"]["completed"] == 10 and p2["totals"]["failed"] == 0 and {r["section_key"]: r["source"] for r in p2["sections"]}["flashcards"] == "generated"


def test_pipeline_marks_sections_served_from_the_shared_cache_as_reused(client, model):
    generate(client, "alice")
    sid = generate(client, "bob")[0][1]["session_id"]                                                                    # bob: identical request -> shared cache, no model calls
    p = client.get(f"{CS}/{sid}/pipeline", headers=H("bob")).json()
    assert p["totals"]["reused"] == 10 and p["totals"]["generated"] == 0 and p["totals"]["input_tokens"] == 0
    assert client.get(f"{CS}/{sid}/pipeline", headers=H("alice")).status_code == 404


# ------------------------------------------------------------------------------------------------ knowledge checks
def test_knowledge_check_uses_the_saved_bank_hides_answers_and_grades_on_the_server(client, model):
    sid = generate(client, topic="Python")[0][1]["session_id"]
    llm_before = len(model.calls)
    r = client.post(f"{CS}/{sid}/sections/summary/knowledge-check", json={"round": 0, "n": 3}, headers=H())
    d = r.json()
    assert r.status_code == 200 and d["available"] is True and len(d["questions"]) == 3 and d["status"] == "open"
    assert not {k for q in d["questions"] for k in q} & {"correct", "correct_options", "explanation", "solution_approach"}
    same = client.post(f"{CS}/{sid}/sections/summary/knowledge-check", json={"round": 0, "n": 3}, headers=H()).json()
    assert same["check_id"] == d["check_id"]                                                                              # refresh-safe
    other = client.post(f"{CS}/{sid}/sections/summary/knowledge-check", json={"round": 1, "n": 3}, headers=H()).json()
    assert other["check_id"] != d["check_id"] and not {q["question_id"] for q in other["questions"]} & {q["question_id"] for q in d["questions"]}
    key = key_of(d["check_id"])
    ans = {qid: (s["correct"][0] if len(s["correct"]) == 1 else s["correct"]) for qid, s in key.items()}
    res = client.post(f"/api/v1/knowledge-checks/{d['check_id']}/submit", json={"answers": ans}, headers=H()).json()
    assert res["score"] == 3 and res["total"] == 3 and res["passed"] is True and all(x["correct"] and x["explanation"] for x in res["results"])
    again = client.post(f"/api/v1/knowledge-checks/{d['check_id']}/submit", json={"answers": {}}, headers=H()).json()   # duplicate submit: stored result
    assert again["score"] == 3
    wrong = {qid: ([j for j in range(len(s["options"])) if j not in s["correct"]][0]) for qid, s in key_of(other["check_id"]).items()}
    assert client.post(f"/api/v1/knowledge-checks/{other['check_id']}/submit", json={"answers": wrong}, headers=H()).json()["passed"] is False
    assert len(model.calls) == llm_before                                                                                 # no model call anywhere in a knowledge check
    assert client.get("/api/v1/assessments", headers=H()).json()["total"] == 0 and client.get(f"{D}/sessions", headers=H()).json()["total"] == 0
    assert client.get("/api/v1/proficiency", headers=H()).json()["items"] == []                                          # never counts as an assessment or evidence


def test_knowledge_check_prefers_the_learners_weak_concept_areas(client, model):
    diag, weak = finish_diagnostic(client)
    sid = generate(client, topic="Python", diagnostic_session_id=diag)[0][1]["session_id"]
    d = client.post(f"{CS}/{sid}/sections/deep_dive/knowledge-check", json={"round": 0, "n": 3}, headers=H()).json()
    concepts = [s["concept"] for s in key_of(d["check_id"]).values()]
    assert concepts.count(weak) >= 2, (weak, concepts)


def test_knowledge_check_without_saved_questions_says_so_and_never_generates(client, model):
    sid = generate(client, topic="Obscure Unbanked Topic")[0][1]["session_id"]
    g = Gen(); get_container().bank.call = g
    d = client.post(f"{CS}/{sid}/sections/summary/knowledge-check", json={"round": 0}, headers=H()).json()
    assert d["available"] is False and d["questions"] == [] and "no saved questions" in d["message"] and g.calls == 0


def test_knowledge_check_ownership_and_validation(client, model):
    sid = generate(client, topic="Python")[0][1]["session_id"]
    assert client.post(f"{CS}/{sid}/sections/summary/knowledge-check", json={}, headers=H("intruder")).status_code == 404
    assert client.post(f"{CS}/{sid}/sections/nope/knowledge-check", json={}, headers=H()).status_code == 404
    assert client.post(f"{CS}/{sid}/sections/summary/knowledge-check", json={"n": 99}, headers=H()).status_code == 422
    d = client.post(f"{CS}/{sid}/sections/summary/knowledge-check", json={}, headers=H()).json()
    assert client.post(f"/api/v1/knowledge-checks/{d['check_id']}/submit", json={"answers": {}}, headers=H("intruder")).status_code == 404
    a = client.post("/api/v1/assessments", json={"skill": "Python", "n_questions": 3}, headers=H()).json()
    assert client.post(f"/api/v1/knowledge-checks/{a['assessment_id']}/submit", json={"answers": {}}, headers=H()).status_code == 404   # only knowledge checks
