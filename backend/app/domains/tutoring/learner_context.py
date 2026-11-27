"""Context Builder: fetch only the learner data that the classified request needs, from the existing LearnAI stores.

Every read is scoped by the authenticated learner id at the repository layer; `build_context` re-checks item ownership. Text that a
learner or a model wrote earlier (saved study material, question explanations) is marked untrusted and fenced. Missing data is stated
explicitly so the model does not fill the gap with invention. Names and e-mail addresses are never sent to the model."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.container import Container
from app.domains.careers.models import CareerRole
from app.domains.learning_intelligence.proficiency import band
from app.domains.learning_intelligence.skill_gap import analyze
from app.domains.roadmap_engine.models import ItemStatus
from app.domains.tutoring.context import ContextItem
from app.domains.tutoring.intent import Classification, SkillRef

DONE = (ItemStatus.COMPLETED, ItemStatus.SKIPPED_MASTERED)
GOAL_ONLY = {"concept_explanation", "comparison", "code_generation", "system_design"}
NEEDS: dict[str, set[str]] = {
    "debugging": {"proficiency"},
    "interview_preparation": {"goal", "proficiency", "gaps", "assessments"},
    "learning_plan": {"goal", "proficiency", "gaps", "roadmap"},
    "learning_recommendation": {"goal", "proficiency", "gaps", "roadmap", "assessments"},
    "skill_gap_analysis": {"goal", "proficiency", "gaps"},
    "progress_review": {"goal", "proficiency", "roadmap", "assessments", "history"},
    "assessment_review": {"assessments", "diagnostic"},
    "quiz_generation": {"proficiency", "assessments"},
    "revision": {"history", "assessments", "diagnostic"},
    "content_generation": {"proficiency"},
    "roadmap_creation": {"goal", "gaps"},
    "roadmap_review": {"goal", "roadmap"},
}
SOURCE_LINKS = {"goal": ("Career goal", "/"), "proficiency": ("Skill Explorer", "/skills"), "gaps": ("Skill gaps", "/skills"), "roadmap": ("Roadmap", "/roadmap"),
                "assessments": ("Assessments", "/assessments"), "diagnostic": ("Diagnostic Study", "/study"), "history": ("Content History", "/history")}


@dataclass
class LearnerContext:
    items: list[ContextItem] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)   # what was actually retrieved, shown to the learner as citations


def skill_refs(c: Container) -> list[SkillRef]:
    return [SkillRef(skill_id=s.skill_id, name=s.name, terms=list(dict.fromkeys([s.name.lower(), s.slug.replace("-", " "), *[a.lower() for a in s.aliases]])))
            for s in c.registry.repo.list(limit=100000)[0]]


def career_for(c: Container, user: str, override: str | None = None) -> CareerRole | None:
    """The goal chosen for this request, otherwise the learner's most recent stored goal."""
    if override:
        return c.careers.get_role(override)
    goals = c.store.list_goals(user)
    return c.careers.get_role(goals[-1]["role_slug"]) if goals else None


def gather(c: Container, user: str, cls: Classification, *, career: str | None = None, focus_skill_id: str | None = None) -> LearnerContext:
    """`focus_skill_id` is the skill picked in the chat header: its level is relevant even when the question names a sub-topic."""
    out = LearnerContext()
    need = set(NEEDS.get(cls.intent, set()))
    if cls.intent in GOAL_ONLY or (cls.intent == "general_knowledge" and cls.skill_id):
        need = {"goal", "proficiency"}
    if not need:
        return out
    learner_focused = cls.intent in NEEDS and cls.intent != "debugging"

    def add(source: str, text: str, priority: float, untrusted: bool = False) -> None:
        out.items.append(ContextItem(owner_id=user, kind=source, text=text, priority=priority, untrusted=untrusted))
        label, link = SOURCE_LINKS[source]
        if not any(s["kind"] == source for s in out.sources):
            out.sources.append({"kind": source, "label": label, "link": link})

    def missing(source: str, text: str) -> None:
        if learner_focused:
            out.items.append(ContextItem(owner_id=user, kind=source, text=text, priority=0.55))

    names = {s.skill_id: s.name for s in c.registry.repo.list(limit=100000)[0]}
    role = career_for(c, user, career) if need & {"goal", "gaps"} else None
    estimates = c.store.estimates(user) if need & {"proficiency", "gaps"} else {}

    if "goal" in need:
        if role:
            add("goal", f"Career goal: {role.title}. Where natural, relate examples to this goal.", 0.95)
        else:
            missing("goal", "No career goal is set for this learner.")
    if "proficiency" in need:
        wanted = list(dict.fromkeys(x for x in (cls.skill_id, focus_skill_id) if x))
        for sid in wanted:
            e = estimates.get(sid)
            if e:
                add("proficiency", f"{names.get(sid)}: proficiency {e.proficiency_score:.1f}/5 ({band(e.proficiency_score)}, confidence {e.confidence_score:.0%}, "
                                   f"{e.evidence_count} pieces of evidence)", 0.9)
            elif learner_focused:
                missing("proficiency", f"No proficiency evidence is stored for {names.get(sid)}.")
        if learner_focused:
            others = sorted(((sid, x) for sid, x in estimates.items() if sid not in wanted), key=lambda kv: kv[1].proficiency_score)
            for sid, x in others[:8]:
                add("proficiency", f"{names.get(sid, sid)}: proficiency {x.proficiency_score:.1f}/5 (confidence {x.confidence_score:.0%})", 0.45)
            if not estimates:
                missing("proficiency", "No skill assessments are stored, so the learner's proficiency is unknown.")
    if "gaps" in need and role:
        skills = {s.skill_id: s for s in c.registry.repo.list(limit=100000)[0]}
        rep = analyze(c.careers.targets_for(role), estimates, skills, c.registry.graph(), c.registry.min_levels())
        for g in rep.items[:5]:
            level = f"is {g.current:.1f}" if g.confidence > 0 else "has not been assessed (level unknown)"
            add("gaps", f"Gap for {role.title}: {g.name} {level}, target {g.target:.1f}" + (" (prerequisites missing)" if g.blocked else ""), 0.75)
        if rep.recommended_next and rep.recommended_next in skills:
            add("gaps", f"Recommended next skill (computed from skill data): {skills[rep.recommended_next].name}", 0.85)
    if "roadmap" in need:
        rms = c.store.list_roadmaps(user)
        if rms:
            rm = rms[0]
            live = [m for m in rm.milestones if m.status != ItemStatus.SUPERSEDED]
            add("roadmap", f"Roadmap for {rm.goal}: {sum(m.status in DONE for m in live)} of {len(live)} milestones done, {rm.hours_per_week:g} h/week", 0.8)
            for m in [m for m in live if m.status in (ItemStatus.PENDING, ItemStatus.IN_PROGRESS)][:3]:
                add("roadmap", f"Upcoming milestone: {m.skill_name} to {m.target_level}/5 (about {m.estimated_hours:g} h)", 0.65)
        else:
            missing("roadmap", "The learner has no roadmap yet.")
    if "assessments" in need:
        attempts, _ = c.store.list_attempts(user, 20)
        done = [a for a in attempts if a.status == "completed" and a.results]
        if cls.skill_id:
            done = sorted(done, key=lambda a: a.skill_id != cls.skill_id)   # stable: newest first within each group
        for a in done[:3]:
            score = sum(r["earned"] for r in a.results) / len(a.results)
            add("assessments", f"Assessment on {names.get(a.skill_id, 'a skill')} ({(a.completed_at or a.started_at).date()}): {score:.0%}", 0.7)
        if done:
            for cr in c.assessments.concept_results(done[0].attempt_id)[:5]:
                add("assessments", f"Concept '{cr['concept']}' in the latest {names.get(done[0].skill_id, '')} assessment: {cr['accuracy']:.0%} accuracy", 0.65, untrusted=True)
            for r in [r for r in done[0].results if not r.get("correct") and not r.get("needs_review")][:3]:
                add("assessments", f"Missed question, explanation: {str(r.get('feedback', ''))[:200]}", 0.55, untrusted=True)
        else:
            missing("assessments", "No completed assessments are stored.")
    if "diagnostic" in need:
        rows, _ = c.assessments.list_sessions(user, "diagnostic", 5)
        done_d = [r for r in rows if r["status"] == "completed" and r["score"] is not None]
        for r in done_d[:3]:
            add("diagnostic", f"Diagnostic study on '{str(r['topic'])[:80]}': {r['score']:.0f}%", 0.6, untrusted=True)
        if not done_d:
            missing("diagnostic", "No completed diagnostic study sessions are stored.")
    if "history" in need and c.history:
        sessions, _ = c.history.list_sessions(user, limit=20)
        if cls.topic:
            t = cls.topic.lower()
            sessions = sorted(sessions, key=lambda s: t not in f"{s['title']} {s['topic']}".lower())
        for s in sessions[:4]:
            add("history", f"Saved study material '{s['title'][:100]}' ({s['completed_sections']}/{s['total_sections']} sections)", 0.6, untrusted=True)
        if sessions and cls.intent == "revision":
            body = c.history.get_contents(user, sessions[0]["id"])
            for key in sorted(body, key=lambda k: (k != "summary", k != "key_concepts", k))[:2]:
                add("history", f"From '{sessions[0]['title'][:80]}', section {key}: {_plain(body[key])[:700]}", 0.7, untrusted=True)
        if not sessions:
            missing("history", "No saved study material exists.")
    return out


def _plain(value) -> str:
    """Flatten stored section content (strings, lists, block dicts) into readable text."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_plain(v) for k, v in value.items() if k not in ("id", "type", "schema_version", "version"))
    if isinstance(value, list):
        return " ".join(_plain(v) for v in value)
    return ""
