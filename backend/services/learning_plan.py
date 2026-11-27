"""Assessment-driven learning plans: a targeted outline built from the skill-gap report (pure), and its generation through the content-history engine.

The plan covers only identified gaps (plus prerequisite refreshers and a practice section) - never a generic full course. Sections are persisted
independently in a normal content session (``metadata.plan`` + the assessment linkage), so history, resume, regenerate and versions all work unchanged."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from app.core.exceptions import ConflictError
from app.domains.assessments.analysis import slug
from app.infrastructure.database.content_history_store import ContentHistoryStore
from app.observability.metrics import metrics
from services import content_generation as cg
from services import session_generation as sg

log = logging.getLogger("learning_plan")
MAX_GAP_SECTIONS = 6
UNKNOWN_PLAN_BELOW = 70.0
LEVEL_FOR = {"strong": "Expert", "good": "Intermediate", "developing": "Intermediate", "needs_improvement": "Beginner", "unknown": "Intermediate"}


def build_plan(report: dict, attempt_id: str) -> dict:
    """Structured outline from a gap report. Raises ConflictError when the assessment identified nothing to target."""
    order = list(report["recommended_learning_order"])[:MAX_GAP_SECTIONS]
    score = report.get("overall_score")
    # Concepts with too little evidence are only worth a plan when the overall result is weak; after a strong attempt they are just "not enough data".
    unknown = [] if order or (score is not None and score >= UNKNOWN_PLAN_BELOW) else [c for c in report.get("unknown_concepts", [])][:MAX_GAP_SECTIONS]
    if not order and not unknown:
        raise ConflictError("This assessment found no skill gaps to target. Take a longer assessment or a harder one to find areas to improve.")
    by = {c["concept"]: c for c in report["concepts"]}
    skill = report["skill"]
    sections: list[dict] = [{"key": "plan-overview", "title": "Your learning plan", "kind": "overview"}]
    for p in report.get("prerequisite_gaps", [])[:2]:
        sections.append({"key": f"prereq-{slug(p['name'])}", "title": f"Prerequisite refresher: {p['name']}", "kind": "prerequisite", "concept": p["name"]})
    for k in order or unknown:
        c = by.get(k, {})
        sections.append({"key": f"gap-{slug(k)}", "title": k, "kind": "concept", "concept": k, "accuracy": c.get("accuracy"), "questions": c.get("questions_attempted"),
                         "priority": "critical" if k in report["critical_gaps"] else "high" if order else "verify"})
    sections.append({"key": "plan-practice", "title": "Practice and knowledge check", "kind": "practice"})
    targets = order or unknown
    return {"source_assessment_id": attempt_id, "target_skill_id": report["skill_id"], "skill": skill, "level": LEVEL_FOR.get(report["overall_level"], "Intermediate"),
            "identified_skill_gaps": order, "critical_gaps": report["critical_gaps"], "unverified_concepts": unknown, "strong_concepts": report["strong_concepts"],
            "recommended_concepts": targets, "overall_score": report["overall_score"],
            "learning_objectives": [f"Explain {k} in {skill} and apply it to a realistic task" for k in targets], "sections": sections}


def create_plan_session(history: ContentHistoryStore, user_id: str, report: dict, attempt_id: str) -> dict:
    plan = build_plan(report, attempt_id)
    title = f"{plan['skill']}: targeted learning plan"[:200]
    return history.create_session(
        user_id, f"{plan['skill']} targeted learning plan", plan["level"], [(s["key"], s["title"]) for s in plan["sections"]], content_type="course", title=title,
        description=f"Based on your {plan['skill']} assessment ({plan['overall_score']}%): " + ", ".join(plan["recommended_concepts"])[:300],
        request={"topic": plan["skill"], "level": plan["level"], "score": round((plan["overall_score"] or 0) / 10)},
        metadata_extra={"plan": plan, "source_assessment_id": attempt_id, "target_skill_id": plan["target_skill_id"], "identified_skill_gaps": plan["identified_skill_gaps"],
                        "recommended_concepts": plan["recommended_concepts"], "concepts": plan["recommended_concepts"], "learning_objectives": plan["learning_objectives"]})


RULES = ("Use plain Markdown only (no HTML). Include, where they genuinely help: a Mermaid diagram in a ```mermaid fenced block (valid syntax, simple flowchart or sequence), "
         "math in KaTeX using $...$ or $$...$$ (only if the concept is mathematical), runnable code in a fenced block with a language tag, one real-world use case, "
         "2-3 practice exercises and 3 knowledge-check questions (put the answers under an '### Answers' heading). Be accurate; never invent APIs; say so if unsure.")


def section_prompt(plan: dict, sec: dict) -> str:
    base = f"You write one section of a personalized learning plan for a {plan['level']} learner who took a diagnostic assessment in {plan['skill']}.\n"
    k = sec["kind"]
    if k == "overview":
        body = (f"SECTION: overview. Summarize in ~200 words why these concepts were chosen and the recommended learning order: {', '.join(plan['recommended_concepts'])}. "
                f"Strong already: {', '.join(plan['strong_concepts']) or 'none identified'}. List the learning objectives. Add one Mermaid flowchart showing the learning order. Do not teach the concepts here.")
    elif k == "prerequisite":
        body = f"SECTION: prerequisite refresher for '{sec['concept']}' needed before the gaps below. Keep it short and practical (~300 words) with one example. {RULES}"
    elif k == "practice":
        body = (f"SECTION: practice. Create a mixed practice set covering ONLY these concepts: {', '.join(plan['recommended_concepts'])}. For each concept: one hands-on exercise and "
                f"one knowledge-check question, answers under '### Answers'. No new theory.")
    else:
        acc = sec.get("accuracy")
        body = (f"SECTION: concept '{sec['concept']}' ({sec['priority']} priority" + (f"; assessment accuracy {round(acc * 100)}% on {sec.get('questions')} questions" if acc is not None else "")
                + f"). Explain it clearly from first principles to practical use at {plan['level']} depth, covering common mistakes. {RULES}")
    return base + body + "\nOutput ONLY the Markdown for this section."


async def stream_plan(store: ContentHistoryStore, user_id: str, session: dict, *, only: Optional[List[str]] = None, force: bool = False, concurrency: Optional[int] = None) -> AsyncIterator[Dict[str, Any]]:
    """Same event protocol and durability rules as ``session_generation.stream_session`` (claim -> generate -> persist -> finish), for plan sections."""
    plan = (session.get("metadata") or {}).get("plan") or {}
    secs = {s["key"]: s for s in plan.get("sections", [])}
    sid = session["id"]
    t0 = time.perf_counter()
    names = [k for k in secs if not only or k in only]
    metas = {m["section_key"]: m for m in (await sg._t(store.list_sections, user_id, sid, list(secs)) or [])}
    done = [k for k in names if metas.get(k, {}).get("status") == "COMPLETED" and not force]
    todo = [k for k in names if k not in done]
    yield {"event": "meta", "data": {"sections": names, "total": len(names), "session_id": sid, "cached": not todo, "reused": done, "generating": todo,
                                     "stages": [{"id": f"section:{k}", "label": secs[k]["title"], "status": "pending"} for k in names]}}
    saved = await sg._t(store.get_contents, user_id, sid, done) if done else {}
    for k in done:
        yield {"event": "section", "data": {"name": k, "value": saved.get(k), "ok": True, "reused": True, "version": metas[k]["content_version"]}}
    claimed: dict[str, str] = {}
    waiting: list[str] = []
    beat = None
    failed: list[str] = []
    try:
        for k in todo:
            target = (metas.get(k, {}).get("content_version") or 0) + 1
            job, _ = await sg._t(store.create_or_get_job, sid, k, f"{sid}:{k}:v{target}")
            if job["status"] in ("FAILED", "CANCELLED"):
                await sg._t(store.requeue_job, job["id"])
            if await sg._t(store.claim_job, job["id"], sg.WORKER_ID, sg.LEASE_S):
                claimed[k] = job["id"]
            else:
                waiting.append(k)
        if claimed:
            beat = asyncio.create_task(sg._heartbeats(store, claimed))
            q: asyncio.Queue = asyncio.Queue()
            sem = asyncio.Semaphore(max(1, concurrency or cg.effective_concurrency()))
            provider, model = sg._model_info()

            async def one(k: str):
                async with sem:
                    sec = cg.Section(k, "markdown", 1800, "")
                    prompt = section_prompt(plan, secs[k])
                    for attempt in (1, 2):
                        try:
                            res = cg._as_result(await cg._call_model(sec, prompt))
                            await q.put((k, cg.parse_section(sec, res.text, res.truncated), res, True))
                            return
                        except asyncio.CancelledError:
                            raise
                        except Exception:  # noqa: BLE001 - retry once, then report the section as failed
                            pass
                    await q.put((k, None, None, False))

            tasks = [asyncio.create_task(one(k)) for k in list(claimed)]
            try:
                for _ in range(len(tasks)):
                    k, value, res, ok = await q.get()
                    jid = claimed.pop(k, None)
                    if ok:
                        meta = await sg._t(store.save_section, user_id, sid, k, value, prompt_version="plan-1", provider=provider, model=model, tokens_in=res.tokens_in,
                                           tokens_out=res.tokens_out, latency_ms=res.latency_ms, job_id=jid, force_new=force)
                        if jid:
                            await sg._t(store.append_event, jid, "section_completed", {"section": k})
                            await sg._t(store.finish_job, jid, "COMPLETED", worker=sg.WORKER_ID)
                        yield {"event": "section", "data": {"name": k, "value": value, "ok": True, "reused": False, "version": meta["content_version"] if meta else None}}
                    else:
                        failed.append(k)
                        if jid:
                            await sg._t(store.append_event, jid, "section_failed", {"section": k})
                            await sg._t(store.finish_job, jid, "FAILED", worker=sg.WORKER_ID, error_code="generation_failed", error_message="section could not be generated")
                        yield {"event": "section", "data": {"name": k, "value": None, "ok": False}}
            finally:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        if waiting:
            deadline = time.monotonic() + sg.LEASE_S
            while waiting and time.monotonic() < deadline:
                await asyncio.sleep(0.4)
                for k in list(waiting):
                    sec = await sg._t(store.get_section, user_id, sid, k)
                    if sec and sec["status"] == "COMPLETED" and (sec["content_version"] or 0) > (metas.get(k, {}).get("content_version") or 0):
                        waiting.remove(k)
                        yield {"event": "section", "data": {"name": k, "value": sec["content"], "ok": True, "reused": True, "version": sec["content_version"]}}
                    elif sec and sec["status"] in ("FAILED", "NOT_STARTED"):
                        waiting.remove(k)
                        failed.append(k)
                        yield {"event": "section", "data": {"name": k, "value": None, "ok": False}}
            failed.extend(k for k in waiting if k not in failed)
        content = await sg._t(store.get_contents, user_id, sid)
        incomplete = [k for k in secs if k not in content]
        metrics.inc("learning_plan_sections_generated_total", len(todo) - len(failed))
        yield {"event": "done", "data": {"incomplete_sections": incomplete, "session_id": sid, "cached": not todo, "elapsed_ms": int((time.perf_counter() - t0) * 1000), "content": None}}
    finally:
        if beat:
            beat.cancel()
        for k, jid in list(claimed.items()):
            try:
                store.finish_job(jid, "CANCELLED", worker=sg.WORKER_ID, error_code="interrupted", error_message="generation was interrupted")
                store.append_event(jid, "interrupted", {"section": k})
            except Exception:  # noqa: BLE001
                log.exception("could not release job %s", jid)
