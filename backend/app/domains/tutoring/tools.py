"""Tool registry: the only way the tutor touches LearnAI modules.

* Tools are selected by the rule-based classifier from the learner's own message, never from model output.
* Arguments are validated by a strict schema; the learner id always comes from authentication, never from arguments.
* Every tool reuses the existing service or endpoint logic of its module. No SQL or code from a model is ever executed.
* Tools that create data (`requires_confirmation`) are only *proposed*; they run after the learner confirms."""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import Response
from pydantic import BaseModel, ConfigDict, Field

from app.agents.safety import ToolGate
from app.core.container import Container
from app.core.exceptions import AppError, ValidationFailure
from app.domains.learning_intelligence.proficiency import band
from app.domains.roadmap_engine.models import ItemStatus
from app.domains.tutoring.intent import Classification
from app.domains.tutoring.learner_context import DONE, career_for
from app.observability.metrics import metrics

log = logging.getLogger("app.tutor.tools")


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(Args):
    pass


class GapArgs(Args):
    role_slug: str = Field(min_length=2, max_length=80)


class AssessmentArgs(Args):
    skill_id: str | None = Field(None, max_length=64)


class RoadmapArgs(Args):
    role_slug: str = Field(min_length=2, max_length=80)
    hours_per_week: float = Field(5, gt=0, le=80)


class QuizArgs(Args):
    skill_id: str = Field(min_length=1, max_length=64)
    n_questions: int = Field(5, ge=3, le=20)


class StudyArgs(Args):
    topic: str = Field(min_length=1, max_length=80)


def result(kind: str, title: str, data: dict, *, link: str | None = None, link_label: str | None = None, empty: str | None = None) -> dict:
    """The structured shape every tool returns and the UI renders as a learning card. `empty` explains why there is nothing to show."""
    return {"kind": kind, "title": title, "data": data, "link": link, "link_label": link_label, "empty": empty}


# ------------------------------------------------------------------ handlers
def _names(c: Container) -> dict[str, str]:
    return {s.skill_id: s.name for s in c.registry.repo.list(limit=100000)[0]}


def analyze_skill_gaps(c: Container, user: str, a: GapArgs, _key: str) -> dict:
    from app.api.v1.routes import skill_gaps
    role = c.careers.get_role(a.role_slug)
    if not role:
        raise ValidationFailure("unknown career role")
    rep = skill_gaps(role.slug, user, c)
    names = _names(c)
    # a skill without evidence has no measured level: report it as unknown, never as zero
    items = [{"name": g.name, "current": g.current if g.confidence > 0 else None, "assessed": g.confidence > 0, "target": g.target, "gap": g.gap, "confidence": g.confidence, "blocked": g.blocked} for g in rep.items[:8]]
    return result("skill_gaps", f"Skill gaps for {role.title}", {"items": items, "total": len(rep.items), "recommended_next": names.get(rep.recommended_next) if rep.recommended_next else None,
                  "assessed_skills": len(c.store.estimates(user))}, link="/skills", link_label="Open Skill Explorer",
                  empty=None if items else "No open gaps: every required skill meets its target.")


def get_progress(c: Container, user: str, _a: NoArgs, _key: str) -> dict:
    from app.api.v1.extra_routes import _summary
    s = _summary(c, user)
    data = {"skills_assessed": s["skills_assessed"], "assessments_completed": s["assessments_completed"], "consistency": s["consistency"],
            "roadmap_completion": s["roadmap_completion"], "strongest": s["strongest"][:3], "weakest": s["weakest"][:3]}
    return result("progress", "Your learning progress", data, link="/analytics", link_label="Open Analytics",
                  empty=None if (s["assessments_completed"] or s["skills_assessed"]) else "No assessment activity is recorded yet.")


def get_assessment_results(c: Container, user: str, a: AssessmentArgs, _key: str) -> dict:
    from app.api.v1.assessment_routes import _results
    attempts, _ = c.store.list_attempts(user, 100)
    done = [x for x in attempts if x.status == "completed" and x.results]
    if a.skill_id:
        done = [x for x in done if x.skill_id == a.skill_id]
    if not done:
        diag, _ = c.assessments.list_sessions(user, "diagnostic", 5)
        row = next((d for d in diag if d["status"] == "completed" and d["score"] is not None), None)
        if not row:
            return result("assessment", "Assessment results", {}, link="/assessments", link_label="Take an assessment", empty="No completed assessment was found.")
        return result("assessment", f"Latest diagnostic study: {row['topic']}", {"score_pct": row["score"], "correct_count": row["correct_answers"], "total_questions": row["total_questions"],
                      "concepts": [], "diagnostic": True}, link=f"/study?session={row['attempt_id']}", link_label="Open the session")
    row = c.assessments.attempt_row(user, done[0].attempt_id)
    r = _results(c, user, row)
    concepts = [{"concept": x["concept"], "accuracy": x["accuracy"], "gap": x.get("identified_gap", False)} for x in r["concepts"]][:10]
    return result("assessment", f"Latest assessment: {r['skill']}", {"score_pct": r["score_pct"], "correct_count": r["correct_count"], "total_questions": r["total_questions"],
                  "level": r["proficiency_level"], "concepts": concepts, "diagnostic": False}, link=f"/assessments/{r['assessment_id']}", link_label="Review answers")


def get_roadmap(c: Container, user: str, _a: NoArgs, _key: str) -> dict:
    rms = c.store.list_roadmaps(user)
    if not rms:
        return result("roadmap", "Your roadmap", {}, link="/roadmap", link_label="Open Roadmap", empty="You do not have a roadmap yet. Ask me to create one.")
    return _roadmap_card(c, rms[0], "Your roadmap")


def _roadmap_card(c: Container, rm, title: str) -> dict:
    live = [m for m in rm.milestones if m.status != ItemStatus.SUPERSEDED]
    role = c.careers.get_role(rm.goal)
    return result("roadmap", f"{title}: {role.title if role else rm.goal}", {"done": sum(m.status in DONE for m in live), "total": len(live), "estimated_weeks": rm.estimated_weeks,
                  "hours_per_week": rm.hours_per_week, "milestones": [{"skill": m.skill_name, "target": m.target_level, "hours": m.estimated_hours, "status": m.status.value} for m in live[:8]]},
                  link="/roadmap", link_label="Open Roadmap")


def generate_roadmap(c: Container, user: str, a: RoadmapArgs, key: str) -> dict:
    from app.api.v1.routes import generate_roadmap as endpoint
    from app.api.v1.schemas import RoadmapGenerateIn
    rm = endpoint(RoadmapGenerateIn(role_slug=a.role_slug, hours_per_week=a.hours_per_week), user, c, key)
    return _roadmap_card(c, rm, "Roadmap created")


async def create_quiz(c: Container, user: str, a: QuizArgs, key: str) -> dict:
    from app.api.v1.assessment_routes import create_assessment
    from app.api.v1.schemas import AssessmentCreateIn
    out = await create_assessment(AssessmentCreateIn(skill=a.skill_id, n_questions=a.n_questions), Response(), user, c, key)
    generating = out["status"] == "generating"
    return result("quiz", f"{out['skill']} quiz ready" if not generating else f"Preparing your {out['skill']} quiz",
                  {"assessment_id": out["assessment_id"], "total_questions": out["total_questions"], "requested": out["requested"], "generating": generating},
                  link=f"/assessments/{out['assessment_id']}", link_label="Start quiz")


def get_learning_history(c: Container, user: str, _a: NoArgs, _key: str) -> dict:
    sessions, total = c.history.list_sessions(user, limit=6)
    items = [{"title": s["title"], "completed": s["completed_sections"], "sections": s["total_sections"], "link": f"/history/{s['id']}"} for s in sessions]
    return result("history", "Your saved study material", {"items": items, "total": total}, link="/history", link_label="Open Content History",
                  empty=None if items else "No saved study material was found.")


def open_study_material(c: Container, user: str, a: StudyArgs, _key: str) -> dict:
    from urllib.parse import quote
    return result("study_link", f"Study guide: {a.topic}", {"topic": a.topic}, link=f"/study?topic={quote(a.topic)}", link_label="Open Diagnostic Study")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[Args]
    handler: Callable[..., Any]
    requires_confirmation: bool = False


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("analyze_skill_gaps", "Compare your proficiency with a career role's requirements", GapArgs, analyze_skill_gaps),
    Tool("get_progress", "Summarise your learning activity", NoArgs, get_progress),
    Tool("get_assessment_results", "Read your latest assessment or diagnostic result", AssessmentArgs, get_assessment_results),
    Tool("get_roadmap", "Read your current roadmap", NoArgs, get_roadmap),
    Tool("generate_roadmap", "Create a new learning roadmap", RoadmapArgs, generate_roadmap, requires_confirmation=True),
    Tool("create_quiz", "Create an assessment from the question bank", QuizArgs, create_quiz, requires_confirmation=True),
    Tool("get_learning_history", "List your saved study material", NoArgs, get_learning_history),
    Tool("open_study_material", "Link to Diagnostic Study with the topic filled in", StudyArgs, open_study_material),
]}
GATE = ToolGate("tutor", set(TOOLS))


async def run_tool(c: Container, user: str, name: str, args: dict, *, key: str) -> tuple[str, dict]:
    """Validate, authorise and execute. Returns ("succeeded", result) or ("failed", {"message": safe text}). Never raises."""
    try:
        GATE.check(name)
        tool = TOOLS[name]
        parsed = tool.args.model_validate(args)
        out = tool.handler(c, user, parsed, key)
        if inspect.isawaitable(out):
            out = await out
    except AppError as e:
        log.warning("tool %s failed: %s", name, e.code)
        metrics.inc("tutor_tool_executions_total", tool=name, status="failed")
        return "failed", {"message": e.message if e.status_code < 500 else "That LearnAI service is unavailable right now. Please try again."}
    except Exception:  # noqa: BLE001  details go to the log only
        log.exception("tool %s failed", name)
        metrics.inc("tutor_tool_executions_total", tool=name, status="failed")
        return "failed", {"message": "The action could not be completed."}
    metrics.inc("tutor_tool_executions_total", tool=name, status="succeeded")
    log.info("tool %s succeeded", name)
    return "succeeded", out


# ------------------------------------------------------------------ planning
@dataclass
class Plan:
    tool: str | None = None
    args: dict | None = None
    confirm_text: str | None = None   # set for actions that must be confirmed
    reply: str | None = None          # a complete deterministic answer: no model call is needed


def plan(c: Container, user: str, cls: Classification, message: str, *, career: str | None = None) -> Plan:
    """Turn the classification into at most one tool call, filling arguments from the learner's message and stored data."""
    import re
    name = cls.tool_name
    if not name:
        return Plan()
    if name in ("analyze_skill_gaps", "generate_roadmap"):
        low = message.lower()
        named = next((r for r in sorted(c.careers.list_roles(), key=lambda r: -len(r.title)) if r.title.lower() in low or r.slug.replace("-", " ") in low), None)
        role = named or career_for(c, user, career)
        if not role:
            options = ", ".join(r.title for r in c.careers.list_roles())
            return Plan(reply="I need a target role for that, and you have not set a career goal. Choose one in the **Career goal** selector above, "
                              f"or name it in your message (for example *“create a roadmap for Data Engineer”*).\n\nAvailable roles: {options}.")
        if name == "analyze_skill_gaps":
            return Plan(name, {"role_slug": role.slug})
        m = re.search(r"(\d{1,2})\s*(?:h|hrs?|hours?)\b", low)
        goals = c.store.list_goals(user)
        hours = float(m.group(1)) if m and 0 < int(m.group(1)) <= 80 else float(goals[-1]["hours_per_week"] if goals else 5)
        return Plan(name, {"role_slug": role.slug, "hours_per_week": hours},
                    confirm_text=f"I can create a **{role.title}** roadmap at **{hours:g} hours per week** from your current skill data. Confirm below to save it.")
    if name == "create_quiz":
        m = re.search(r"\b(\d{1,2})\s*(?:-\s*)?(?:question|mcq)", message, re.I)
        n = min(20, max(3, int(m.group(1)))) if m else 5
        skill = c.registry.repo.get(cls.skill_id)
        return Plan(name, {"skill_id": skill.skill_id, "n_questions": n},
                    confirm_text=f"I can create a **{n}-question {skill.name} quiz** from the LearnAI question bank. Confirm below to start it, "
                                 "or ask me to *quiz you one question at a time* to practise here instead.")
    if name == "get_assessment_results":
        return Plan(name, {"skill_id": cls.skill_id})
    if name == "open_study_material":
        topic = (cls.topic or "").strip()
        if not topic:
            return Plan()
        return Plan(name, {"topic": topic[:80]},
                    reply=f"Study guides are generated in **Diagnostic Study**, where they are saved to your Content History. Use the card below to start one on **{topic[:80]}**.")
    return Plan(name, {})


def evidence_note(c: Container, user: str, skill_id: str | None) -> str | None:
    """A short, factual label of the learner's stored level for the focus topic (shown in the UI)."""
    e = c.store.estimates(user).get(skill_id) if skill_id else None
    return f"{band(e.proficiency_score)} ({e.proficiency_score:.1f}/5)" if e else None
