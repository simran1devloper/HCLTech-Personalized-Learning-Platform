"""
services/content_service.py — Study material generation (section by section) and history management
"""

import asyncio
import logging
import time
from contextlib import aclosing
from typing import Any, AsyncIterator, Dict, List, Optional
from datetime import datetime, timezone

from services import content_generation as cg
from services import prompt_registry as pr
from utils.supabase_client import get_supabase

log = logging.getLogger("content_service")


def _cache():
    from app.core.container import get_container
    return get_container().cache


async def _record_history(user_id: str, topic: str, score: int, level: str) -> None:
    try:  # non-critical; sync client kept off the event loop
        await asyncio.to_thread(_save_history, user_id, topic, score, level)
    except Exception as e:
        log.warning("failed to save learning history: %s", type(e).__name__)


async def generate_content(topic: str, level: str, score: int, user_id: str) -> Dict[str, Any]:
    """Full study material (all sections). Served from cache when an identical, complete set exists.
    Raises RuntimeError if no section at all could be generated (AI unavailable)."""
    key = cg.cache_key(topic, level, score)
    content = await _cache().get(key, "study-content")
    if content is None:
        sections, failed = {}, []
        async for name, value, ok in cg.iter_sections(topic, level, score):
            sections[name] = value
            if not ok:
                failed.append(name)
        if len(failed) == len(cg.SECTION_NAMES):
            raise RuntimeError("no section could be generated")
        content = cg.assemble(sections, topic, level, score)
        content["incomplete_sections"] = [n for n in cg.SECTION_NAMES if n in failed]
        if not failed:
            await _cache().set(key, content, _ttl(), "study-content")
    await _record_history(user_id, topic, score, level)
    return {**content, "topic": topic, "level": level}


def _ttl() -> int:
    from app.core.config import get_settings
    return get_settings().CONTENT_CACHE_TTL_S


async def stream_content(topic: str, level: str, score: int, user_id: str,
                         only: Optional[List[str]] = None) -> AsyncIterator[Dict[str, Any]]:
    """Pipeline event stream: meta -> (stage | section)* -> done.
    Stages: plan -> one per section -> review (validate & assemble) -> save. `only` regenerates just those sections."""
    from services.pipeline import stage_list

    def stage(sid: str, label: str, status: str, **kw) -> Dict[str, Any]:
        return {"event": "stage", "data": {"id": sid, "label": label, "status": status, **kw}}

    names = [n for n in cg.SECTION_NAMES if not only or n in only]
    key = cg.cache_key(topic, level, score)
    stages = stage_list(("plan", "Plan the study guide"), *[(f"section:{n}", cg.LABELS[n], pr.section_icon(n)) for n in names],
                        ("review", "Validate and assemble"), ("save", "Save to history and cache"))
    t0 = time.perf_counter()
    yield {"event": "meta", "data": {"sections": names, "total": len(names), "stages": stages, "workflow": pr.guide_prompt_fingerprint()}}

    yield stage("plan", "Plan the study guide", "running", detail="Checking for a saved copy of this study guide…")
    cached = None if only else await _cache().get(key, "study-content")
    if cached is not None:
        yield stage("plan", "Plan the study guide", "done", detail="Found a complete saved copy — no model calls needed")
        for n in names:
            yield stage(f"section:{n}", cg.LABELS[n], "done", detail="From saved copy")
            yield {"event": "section", "data": {"name": n, "value": cached[n], "ok": True}}
        yield stage("review", "Validate and assemble", "done", detail="Already validated when it was first generated")
        await _record_history(user_id, topic, score, level)
        yield stage("save", "Save to history and cache", "done", detail="History updated")
        yield {"event": "done", "data": {"incomplete_sections": [], "cached": True, "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                                         "content": {**cached, "topic": topic, "level": level}}}
        return
    yield stage("plan", "Plan the study guide", "done",
                detail=f"{len(names)} section{'s' if len(names) != 1 else ''} · {level} depth · generating up to {_concurrency()} at a time")

    sections, failed = {}, []
    # aclosing: if the client disconnects, the pipeline is closed and in-flight model requests are cancelled
    async with aclosing(cg.run_pipeline(topic, level, score, only=names)) as events:
        async for ev in events:
            if ev["event"] == "section":
                d = ev["data"]
                sections[d["name"]] = d["value"]
                if not d["ok"]:
                    failed.append(d["name"])
            yield ev

    failed = [n for n in cg.SECTION_NAMES if n in failed]
    ok_count = len(names) - len(failed)
    yield stage("review", "Validate and assemble", "done" if not failed else "failed",
                detail=f"{ok_count} of {len(names)} sections valid" + (f"; {len(failed)} need regeneration" if failed else ""))
    if only:  # partial regeneration: caller merges into what it already has
        yield stage("save", "Save to history and cache", "skipped", detail="Partial regeneration is not cached")
        yield {"event": "done", "data": {"incomplete_sections": failed, "content": None, "elapsed_ms": int((time.perf_counter() - t0) * 1000)}}
        return
    yield stage("save", "Save to history and cache", "running")
    content = cg.assemble(sections, topic, level, score)
    content["incomplete_sections"] = failed
    detail = []
    if not failed:
        await _cache().set(key, content, _ttl(), "study-content")
        detail.append("cached for reuse")
    else:
        detail.append("not cached (incomplete)")
    if ok_count:
        await _record_history(user_id, topic, score, level)
        detail.append("history updated")
    yield stage("save", "Save to history and cache", "done", detail=", ".join(detail))
    yield {"event": "done", "data": {"incomplete_sections": failed, "cached": False, "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                                     "content": {**content, "topic": topic, "level": level}}}


def _concurrency() -> int:
    return cg.effective_concurrency()


def _save_history(user_id: str, topic: str, score: int, level: str) -> None:
    """
    Insert a learning session record into Supabase.

    Args:
        user_id: User UUID.
        topic: Topic string.
        score: Quiz score.
        level: Classified level.
    """
    db = get_supabase()
    db.table("learning_history").insert({
        "user_id":    user_id,
        "topic":      topic,
        "score":      score,
        "level":      level,
        "created_at": datetime.now(timezone.utc).isoformat()
    }).execute()


def get_history(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Retrieve a user's learning history from Supabase.

    Args:
        user_id: User UUID.
        limit: Maximum records to return.

    Returns:
        List of history records sorted by most recent first.
    """
    db = get_supabase()
    result = (
        db.table("learning_history")
          .select("id, topic, score, level, created_at")
          .eq("user_id", user_id)
          .order("created_at", desc=True)
          .limit(limit)
          .execute()
    )
    return result.data or []
