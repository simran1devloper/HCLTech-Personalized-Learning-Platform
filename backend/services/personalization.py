"""Learner profile for study material: career goal, level, and what the diagnostic showed (weak / strong concept areas).

Nothing here is career- or topic-specific: the career title comes from the career catalogue entry the learner chose, the concept areas from their own diagnostic
results. The profile (1) adds one compact line to each section prompt, (2) makes weak-area sections deeper and expert-level overview sections shorter, and (3) is
fingerprinted into the shared cache key so two learners only share generated text when their profiles match. No model call is involved."""
from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

DEEP_SECTIONS = ("deep_dive", "practical_guide", "viva_voice")      # prose sections where weak areas deserve more depth
BRIEF_SECTIONS = ("summary", "learning_roadmap")                      # an expert with no weak areas needs less orientation
MAX_LISTED = 5


def empty_profile(level: str = "", diagnostic_session_id: str | None = None) -> dict:
    return {"career": None, "level": level, "weak": [], "strong": [], "diagnostic_session_id": diagnostic_session_id}


def build_profile(c, user_id: str, level: str, diagnostic_session_id: str | None = None) -> dict:
    """Assemble the profile from stored data only (goals, the learner's own diagnostic concept results). Ownership is enforced: a foreign session id is ignored."""
    p = empty_profile(level)
    goals = c.store.list_goals(user_id)
    if goals:
        role = c.careers.get_role(goals[-1]["role_slug"])
        if role:
            p["career"] = {"slug": role.slug, "title": role.title}
    if diagnostic_session_id:
        a = c.assessments.attempt_row(user_id, diagnostic_session_id)
        if a and a.get("kind") == "diagnostic" and a["status"] == "completed":
            rows = c.assessments.concept_results(diagnostic_session_id)
            p["weak"] = [r["concept"] for r in rows if r["proficiency_level"] == "needs_improvement"][:MAX_LISTED]
            p["strong"] = [r["concept"] for r in rows if r["proficiency_level"] in ("strong", "good")][:MAX_LISTED]
            p["diagnostic_session_id"] = diagnostic_session_id
    return p


def fingerprint(profile: dict | None) -> str:
    """Stable id of everything in the profile that changes generated text; '' for a profile that adds nothing (so unpersonalised caches keep working)."""
    if not profile or not (profile.get("career") or profile.get("weak") or profile.get("strong")):
        return ""
    raw = f"{(profile.get('career') or {}).get('slug', '')}|{','.join(sorted(profile.get('weak', [])))}|{','.join(sorted(profile.get('strong', [])))}"
    return "p" + hashlib.sha256(raw.encode()).hexdigest()[:10]


def depth_for(profile: dict | None, section: str) -> str:
    if not profile:
        return "standard"
    if profile.get("weak") and section in DEEP_SECTIONS:
        return "deep"
    if not profile.get("weak") and profile.get("level") == "Expert" and section in BRIEF_SECTIONS:
        return "brief"
    return "standard"


def prompt_line(profile: dict | None, section: str) -> str:
    """One compact line appended to the request-specific tail of a section prompt ('' when there is nothing to personalise)."""
    if not profile or not fingerprint(profile):
        return ""
    bits = []
    if profile.get("career"):
        bits.append(f"career goal: {profile['career']['title']}")
    if profile.get("strong"):
        bits.append(f"already strong (do not re-teach): {', '.join(profile['strong'])}")
    if profile.get("weak"):
        bits.append(f"weak areas to cover in more depth: {', '.join(profile['weak'])}")
    d = depth_for(profile, section)
    line = "\nLearner profile: " + "; ".join(bits) + "."
    if d != "standard":
        line += f" Depth for this section: {d}."
    if profile.get("career"):
        line += f" Where natural, relate examples to the {profile['career']['title']} role."
    return line


def scale_section(sec, profile: dict | None):
    """Deeper sections get more output budget, brief ones less (markdown only; JSON sections keep their schema-sized budgets)."""
    if getattr(sec, "kind", "") != "markdown":
        return sec
    d = depth_for(profile, sec.name)
    if d == "deep":
        return replace(sec, max_tokens=int(sec.max_tokens * 1.25))
    if d == "brief":
        return replace(sec, max_tokens=max(300, int(sec.max_tokens * 0.75)))
    return sec


def public_summary(profile: dict | None) -> dict[str, Any]:
    """What is stored with the session and shown to the learner (never raw answers)."""
    p = profile or empty_profile()
    return {"career": p.get("career"), "level": p.get("level"), "weak": p.get("weak", []), "strong": p.get("strong", []), "diagnostic_session_id": p.get("diagnostic_session_id"),
            "fingerprint": fingerprint(p)}
