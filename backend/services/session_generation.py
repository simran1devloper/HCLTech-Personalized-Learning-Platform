"""
services/session_generation.py — the content-history layer over the existing section pipeline (services/content_generation.py).

There is still exactly one generation engine. This module decides *whether* it needs to run:

  1. find-or-create the user's persistent session (reload / reconnect never forks a new one),
  2. reuse every section already stored (zero model calls), or fill from the shared content cache,
  3. for the rest: create/claim one durable job per (session, section, target version), run the pipeline only for those,
  4. persist each section the moment it completes, so an interruption loses at most the in-flight sections.

The database is the source of truth; nothing here keeps state in process memory except the heartbeat task of a running request.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import aclosing
from typing import Any, AsyncIterator, Dict, List, Optional

from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.infrastructure.database.content_history_store import EXTRA_PREFIX, ContentHistoryStore
from app.observability.metrics import metrics
from services import content_generation as cg
from services import personalization as pz
from services import prompt_registry as pr

log = logging.getLogger("session_generation")

LEASE_S = 60.0
WORKER_ID = f"api-{os.getpid()}-{uuid.uuid4().hex[:6]}"
SECTION_TITLES = {"summary": "Summary", "key_concepts": "Key concepts", "deep_dive": "Deep dive", "real_world_applications": "Real-world applications",
                  "viva_voice": "Interview Q&A", "practical_guide": "Practical implementation guide", "learning_roadmap": "Roadmap", "flashcards": "Flashcards",
                  "recommendations": "Recommendations", "agent_intro": "Tutor intro"}
SECTION_SPEC = [(n, SECTION_TITLES[n]) for n in cg.SECTION_NAMES]
_last_recovery = 0.0


def is_known_section(key: str) -> bool:
    return key in cg.SECTION_NAMES or key.startswith(EXTRA_PREFIX)


def recover_abandoned(store: ContentHistoryStore, min_interval_s: float = 15.0) -> int:
    """Expired-lease jobs (worker crashed / API restarted) become resumable. Rate-limited per process; safe from any process."""
    global _last_recovery
    now = time.monotonic()
    if now - _last_recovery < min_interval_s:
        return 0
    _last_recovery = now
    n = store.recover_abandoned()
    if n:
        metrics.inc("content_history_jobs_recovered_total", n)
    return n


def open_session(store: ContentHistoryStore, user_id: str, topic: str, level: str, score: int, *, session_id: str | None = None, new_session: bool = False,
                 content_type: str = "study_guide", language: str = "en") -> tuple[dict, bool]:
    """(session, created). With ``session_id``: that session or NotFoundError. Otherwise reuse the user's live session for the same
    topic+level unless a separate one is explicitly requested. The original request is persisted with the session."""
    recover_abandoned(store)
    if session_id:
        s = store.get_session(user_id, session_id)
        if not s:
            raise NotFoundError("content session not found")
        return s, False
    if not new_session:
        s = store.find_reusable_session(user_id, topic, level, content_type)
        if s:
            return s, False
    s = store.create_session(user_id, topic, level, SECTION_SPEC, content_type=content_type, language=language, request={"topic": topic, "level": level, "score": score})
    metrics.inc("content_history_sessions_created_total")
    return s, True


def request_of(session: dict) -> tuple[str, str, int]:
    r = (session.get("metadata") or {}).get("request") or {}
    return r.get("topic") or session["topic"], r.get("level") or session["learning_level"], int(r.get("score", 5))


def _model_info() -> tuple[str, str]:
    try:
        from app.ai.runtime import get_router
        p = get_router().providers[0]
        return getattr(p, "name", type(p).__name__), getattr(p, "model", "")
    except Exception:  # noqa: BLE001 - metadata only
        return "", ""


def _stage(sid: str, label: str, status: str, **kw) -> Dict[str, Any]:
    return {"event": "stage", "data": {"id": sid, "label": label, "status": status, **kw}}


async def _t(fn, *a, **kw):
    """Store calls are short blocking DB calls; keep them off the event loop."""
    return await asyncio.to_thread(fn, *a, **kw)


async def _heartbeats(store: ContentHistoryStore, jobs: dict[str, str]):
    try:
        while True:
            await asyncio.sleep(LEASE_S / 3)
            for jid in list(jobs.values()):
                await _t(store.heartbeat, jid, WORKER_ID, LEASE_S)
    except asyncio.CancelledError:
        pass


async def stream_session(store: ContentHistoryStore, user_id: str, session: dict, *, only: Optional[List[str]] = None, force: bool = False,
                         created: bool = False, profile: Optional[dict] = None) -> AsyncIterator[Dict[str, Any]]:
    """meta -> (stage | section)* -> done, the same event protocol as the legacy stream plus ``session_id`` / ``reused`` / ``version``.

    ``only``: restrict to these sections.  ``force``: regenerate them even if complete (a new version; the old one is kept)."""
    topic, level, score = request_of(session)
    sid = session["id"]
    fp = pz.fingerprint(profile)
    t0 = time.perf_counter()
    names = [n for n in cg.SECTION_NAMES if not only or n in only]
    metas = {m["section_key"]: m for m in (await _t(store.list_sections, user_id, sid, cg.SECTION_NAMES) or [])}
    done_names = [n for n in names if metas.get(n, {}).get("status") == "COMPLETED"]
    todo = [n for n in names if force or n not in done_names]
    if force:
        done_names = [n for n in done_names if n not in todo]

    from services.pipeline import stage_list
    stages = stage_list(("plan", "Plan the study guide"), *[(f"section:{n}", cg.LABELS[n], pr.section_icon(n)) for n in names], ("review", "Validate and assemble"), ("save", "Save to history and cache"))
    yield {"event": "meta", "data": {"sections": names, "total": len(names), "stages": stages, "workflow": pr.guide_prompt_fingerprint(), "session_id": sid,
                                     "session_created": created, "cached": not todo, "reused": done_names, "generating": todo}}
    yield _stage("plan", "Plan the study guide", "running", detail="Looking for saved sections…")
    metrics.inc("content_history_sections_reused_total", len(done_names))

    # 1. saved sections: straight from the database, no model call
    saved = await _t(store.get_contents, user_id, sid, done_names) if done_names else {}
    detail = (f"{len(done_names)} saved section{'s' if len(done_names) != 1 else ''} reused; generating {len(todo)}" if todo and done_names
              else "Found a complete saved copy — no model calls needed" if not todo else f"{len(todo)} section{'s' if len(todo) != 1 else ''} to generate")
    yield _stage("plan", "Plan the study guide", "done", detail=detail)
    for n in done_names:
        yield _stage(f"section:{n}", cg.LABELS[n], "done", detail="Saved earlier")
        yield {"event": "section", "data": {"name": n, "value": saved.get(n), "ok": True, "reused": True, "version": metas[n]["content_version"]}}

    failed: list[str] = []
    claimed: dict[str, str] = {}           # section -> job id we hold
    beat = None
    try:
        # 2. shared (non-personal) cache: fills missing sections without a model call
        if todo and not force and not only:
            cached = await _shared_cache_get(topic, level, score, fp)
            if cached:
                for n in list(todo):
                    if n in cached:
                        meta = await _t(store.save_section, user_id, sid, n, cached[n], prompt_version=pr.prompt_version(n))
                        yield _stage(f"section:{n}", cg.LABELS[n], "done", detail="From shared saved copy")
                        yield {"event": "section", "data": {"name": n, "value": cached[n], "ok": True, "reused": True, "version": meta["content_version"] if meta else None}}
                        todo.remove(n)

        # 3. one durable job per (session, section, target version); only the claimant generates
        waiting: list[str] = []
        for n in todo:
            target = (metas.get(n, {}).get("content_version") or 0) + 1
            job, _created = await _t(store.create_or_get_job, sid, n, f"{sid}:{n}:v{target}")
            if job["status"] in ("FAILED", "CANCELLED"):
                await _t(store.requeue_job, job["id"])
            if await _t(store.claim_job, job["id"], WORKER_ID, LEASE_S):
                claimed[n] = job["id"]
                await _t(store.append_event, job["id"], "started", {"section": n, "target_version": target})
            else:
                waiting.append(n)  # another live request owns it (or it just completed): never generate twice
        metrics.inc("content_history_jobs_deduplicated_total", len(waiting))
        if claimed:
            beat = asyncio.create_task(_heartbeats(store, claimed))

        # 4. generate exactly the claimed sections; persist each as it completes
        tokens: dict[str, tuple[int, int]] = {}
        provider, model = _model_info()
        if claimed:
            async with aclosing(cg.run_pipeline(topic, level, score, only=list(claimed), profile=profile)) as events:
                async for ev in events:
                    d = ev["data"]
                    if ev["event"] == "stage" and str(d.get("id", "")).startswith("section:") and d.get("status") == "done":
                        tokens[d["id"][8:]] = (int(d.get("tokens_in") or 0), int(d.get("tokens_out") or 0))
                    if ev["event"] != "section":
                        yield ev
                        continue
                    n, value, ok = d["name"], d["value"], d["ok"]
                    job_id = claimed.pop(n, None)
                    if ok:
                        tin, tout = tokens.get(n, (0, 0))
                        meta = await _t(store.save_section, user_id, sid, n, value, prompt_version=pr.prompt_version(n), provider=provider, model=model, tokens_in=tin,
                                        tokens_out=tout, job_id=job_id, force_new=force)
                        if job_id:
                            await _t(store.append_event, job_id, "section_completed", {"section": n, "version": meta["content_version"] if meta else None})
                            await _t(store.finish_job, job_id, "COMPLETED", worker=WORKER_ID)
                        yield {"event": "section", "data": {"name": n, "value": value, "ok": True, "reused": False, "version": meta["content_version"] if meta else None}}
                    else:  # the placeholder text is NOT persisted: previous content (if any) stays active
                        failed.append(n)
                        deferred = bool(d.get("skipped"))  # deferred by the time budget: not an error, generate on demand
                        if job_id:
                            await _t(store.append_event, job_id, "section_deferred" if deferred else "section_failed", {"section": n})
                            await _t(store.finish_job, job_id, "FAILED", worker=WORKER_ID,
                                     error_code="deferred_time_budget" if deferred else "generation_failed",
                                     error_message="deferred to stay within the time budget" if deferred else "section could not be generated")
                        yield {"event": "section", "data": {"name": n, "value": value, "ok": False, **({"skipped": True} if deferred else {})}}
            todo = [n for n in todo if n not in waiting]

        # 5. sections another request was generating: wait for the database, do not duplicate the work
        if waiting:
            yield _stage("plan", "Plan the study guide", "running", detail=f"{len(waiting)} section(s) are already being generated by another request — waiting")
            deadline = time.monotonic() + LEASE_S
            while waiting and time.monotonic() < deadline:
                await asyncio.sleep(0.4)
                for n in list(waiting):
                    sec = await _t(store.get_section, user_id, sid, n)
                    if sec and sec["status"] == "COMPLETED" and (sec["content_version"] or 0) > (metas.get(n, {}).get("content_version") or 0):
                        waiting.remove(n)
                        yield {"event": "section", "data": {"name": n, "value": sec["content"], "ok": True, "reused": True, "version": sec["content_version"]}}
                    elif sec and sec["status"] in ("FAILED", "NOT_STARTED"):
                        waiting.remove(n)
                        failed.append(n)
                        yield {"event": "section", "data": {"name": n, "value": None, "ok": False}}
            failed.extend(n for n in waiting if n not in failed)

        ok_count = len(names) - len(failed)
        yield _stage("review", "Validate and assemble", "done" if not failed else "failed", detail=f"{ok_count} of {len(names)} sections valid" + (f"; {len(failed)} need regeneration" if failed else ""))
        content_all = await _t(store.get_contents, user_id, sid)
        complete = all(n in content_all for n in cg.SECTION_NAMES)
        cached_now = False
        if complete and not only and not force:
            cached_now = True
            await _shared_cache_set(topic, level, score, cg.assemble(content_all, topic, level, score), fp)
        yield _stage("save", "Save to history and cache", "done", detail=f"{len(content_all)} of {len(cg.SECTION_NAMES)} sections saved in your history, "
                     + ("cached for reuse" if cached_now else "not cached (incomplete)" if not complete else "history updated") + (", history updated" if cached_now or not complete else ""))
        out = cg.assemble({k: v for k, v in content_all.items() if k in cg.SECTION_NAMES}, topic, level, score)
        incomplete = [n for n in cg.SECTION_NAMES if n not in content_all]
        out["incomplete_sections"] = incomplete
        yield {"event": "done", "data": {"incomplete_sections": incomplete if not only else failed, "cached": not todo and not failed and not claimed and not force,
                                         "elapsed_ms": int((time.perf_counter() - t0) * 1000), "session_id": sid, "content": None if only else {**out, "topic": topic, "level": level}}}
    finally:
        if beat:
            beat.cancel()
        for n, jid in list(claimed.items()):  # client left / cancelled: release the lease now so the user can resume immediately
            try:
                store.finish_job(jid, "CANCELLED", worker=WORKER_ID, error_code="interrupted", error_message="generation was interrupted")
                store.append_event(jid, "interrupted", {"section": n})
            except Exception:  # noqa: BLE001
                log.exception("could not release job %s", jid)


def _cache():
    from app.core.container import get_container
    return get_container().cache


def _ttl() -> int:
    return get_settings().CONTENT_CACHE_TTL_S


def _cache_key(topic, level, score, fp: str = "") -> str:
    """Shared (non-identifying) cache key; learners only share generated text when their profile fingerprint matches ('' = unpersonalised)."""
    return cg.cache_key(topic, level, score) + (f":{fp}" if fp else "")


async def _shared_cache_get(topic, level, score, fp: str = ""):
    return await _cache().get(_cache_key(topic, level, score, fp), "study-content")


async def _shared_cache_set(topic, level, score, content, fp: str = ""):
    await _cache().set(_cache_key(topic, level, score, fp), content, _ttl(), "study-content")


def persist_complete_content(store: ContentHistoryStore, user_id: str, topic: str, level: str, score: int, content: dict, incomplete: list[str]) -> str:
    """Used by the non-streaming legacy endpoint: save what it generated into the same session model."""
    s, _ = open_session(store, user_id, topic, level, score)
    for n in cg.SECTION_NAMES:
        if n not in incomplete and n in content:
            store.save_section(user_id, s["id"], n, content[n], prompt_version=pr.prompt_version(n))
    return s["id"]


# ----------------------------------------------------------------------------------------------- follow-up requests
def _context_for(store: ContentHistoryStore, user_id: str, session: dict) -> str:
    """Only what a follow-up needs: topic, level, section titles and key-concept terms — never the whole generated course."""
    ctx = session.get("context") or {}
    concepts = (session.get("metadata") or {}).get("concepts") or []
    done = ctx.get("generated_sections") or []
    return (f"Existing study guide: {session['topic']} ({session['learning_level']}). Sections already covered: {', '.join(done) or 'none'}. "
            f"Key concepts already explained: {', '.join(concepts[:20]) or 'none'}. Do not repeat the guide; add only what is asked.")


async def stream_followup(store: ContentHistoryStore, user_id: str, session: dict, instruction: str) -> AsyncIterator[Dict[str, Any]]:
    """Generate one additional section from a free-text request and attach it to the session. Existing sections are not touched."""
    sid = session["id"]
    key = f"{EXTRA_PREFIX}{uuid.uuid4().hex[:8]}"
    title = instruction.strip()[:100]
    await _t(store.add_section, user_id, sid, key, title)
    yield {"event": "meta", "data": {"sections": [key], "total": 1, "session_id": sid, "cached": False, "stages": [{"id": f"section:{key}", "label": title, "status": "pending"}]}}
    job, _ = await _t(store.create_or_get_job, sid, key, f"{sid}:{key}:v1")
    jid = job["id"]
    if not await _t(store.claim_job, jid, WORKER_ID, LEASE_S):
        yield {"event": "error", "data": {"message": "This request is already being processed."}}
        return
    beat = asyncio.create_task(_heartbeats(store, {key: jid}))
    finished = False
    try:
        sec = cg.Section(key, "markdown", 1400, "Answer the learner's request precisely, in markdown, with a short practical example where it helps.")
        prompt = (f"{_context_for(store, user_id, session)}\n---\nLearner request: {instruction.strip()}\nLearner level: {session['learning_level']}\n"
                  "Write a focused markdown section answering the request.")
        yield _stage(f"section:{key}", title, "running", detail="Waiting for the model…")
        res = cg._as_result(await cg._call_model(sec, prompt))
        value = cg.parse_section(sec, res.text, res.truncated)
        provider, model = _model_info()
        meta = await _t(store.save_section, user_id, sid, key, value, prompt_version="followup-1", provider=provider, model=model, tokens_in=res.tokens_in, tokens_out=res.tokens_out,
                        latency_ms=res.latency_ms, job_id=jid)
        await _t(store.append_event, jid, "section_completed", {"section": key})
        await _t(store.finish_job, jid, "COMPLETED", worker=WORKER_ID)
        finished = True
        yield _stage(f"section:{key}", title, "done")
        yield {"event": "section", "data": {"name": key, "title": title, "value": value, "ok": True, "version": meta["content_version"] if meta else 1}}
        yield {"event": "done", "data": {"incomplete_sections": [], "session_id": sid, "section_key": key}}
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("follow-up failed: %s", type(e).__name__)
        await _t(store.finish_job, jid, "FAILED", worker=WORKER_ID, error_code="generation_failed", error_message=type(e).__name__)
        finished = True
        yield {"event": "error", "data": {"message": "The AI service is temporarily unavailable. Please try again."}}
    finally:
        beat.cancel()
        if not finished:
            store.finish_job(jid, "CANCELLED", worker=WORKER_ID, error_code="interrupted")
