"""Benchmark scenarios over the real streaming content path (services.content_service.stream_content)."""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List

from benchmarks.fake_model import FakeModel
from benchmarks.stats import summarise

os.environ.setdefault("DATABASE_PATH", ":memory:")
os.environ.setdefault("ENVIRONMENT", "test")

from app.core.container import get_container, reset_container  # noqa: E402
from app.infrastructure.database.session import get_database  # noqa: E402
from sqlalchemy import text  # noqa: E402
from app.observability.metrics import metrics  # noqa: E402
from services import content_generation as cg  # noqa: E402
from services.content_service import stream_content  # noqa: E402

USER = "bench-user"
LEVEL, SCORE = "Intermediate", 6


def fresh_environment() -> None:
    """New process-like state: container, cache and database are recreated (an in-memory DB loses everything)."""
    reset_container()
    get_container()
    with get_database().begin() as c:
        c.execute(text("INSERT INTO users (id,name,email,password_hash,created_at) VALUES (:i,'Bench User','bench@example.com','x','2026-01-01T00:00:00+00:00') "
                       "ON CONFLICT (id) DO NOTHING"), {"i": USER})
    metrics.reset()


async def consume(topic: str, scale: float, only: List[str] | None = None) -> Dict[str, Any]:
    """Runs one streamed generation; returns timings in *simulated* seconds and the event facts."""
    t0 = time.perf_counter()
    section_at: Dict[str, float] = {}
    first_section = None
    final: Dict[str, Any] = {}
    stage_events = 0
    async for ev in stream_content(topic, LEVEL, SCORE, USER, only=only):
        now = (time.perf_counter() - t0) / scale
        if ev["event"] == "section":
            section_at[ev["data"]["name"]] = now
            first_section = now if first_section is None else first_section
        elif ev["event"] == "stage":
            stage_events += 1
        elif ev["event"] == "done":
            final = ev["data"]
    total = (time.perf_counter() - t0) / scale
    ordered = sorted(section_at.values())
    gaps = [b - a for a, b in zip([0.0] + ordered, ordered)]
    return {"time_to_first_section_s": first_section, "total_s": total, "section_completion_s": section_at,
            "inter_section_gaps_s": gaps, "stage_events": stage_events, "cached": final.get("cached"),
            "incomplete_sections": final.get("incomplete_sections")}


def _tokens(model: FakeModel) -> Dict[str, int]:
    return {"tokens_in": sum(r["tokens_in"] for r in model.ledger), "tokens_out": sum(r["tokens_out"] for r in model.ledger)}


def _metrics_tokens() -> Dict[str, int]:
    return {"tokens_in": int(sum(metrics.get("content_tokens_in_total", section=n) for n in cg.SECTION_NAMES)),
            "tokens_out": int(sum(metrics.get("content_tokens_out_total", section=n) for n in cg.SECTION_NAMES))}


async def cold_generation(model: FakeModel, reps: int) -> Dict[str, Any]:
    runs = []
    for i in range(reps):
        fresh_environment(); model.reset()
        r = await consume(f"benchmark topic {i}", model.time_scale)
        runs.append({**r, "model_calls": model.calls, **_tokens(model), "metrics_tokens": _metrics_tokens()})
    per_section = [t for r in runs for t in r["section_completion_s"].values()]
    return {"runs": runs, "summary": {
        "time_to_first_section_s": summarise(r["time_to_first_section_s"] for r in runs),
        "total_s": summarise(r["total_s"] for r in runs),
        "section_completion_s": summarise(per_section),
        "model_calls": runs[0]["model_calls"], "tokens_in": runs[0]["tokens_in"], "tokens_out": runs[0]["tokens_out"],
        "usage_matches_ledger": all(r["metrics_tokens"] == {k: r[k] for k in ("tokens_in", "tokens_out")} for r in runs)}}


async def repeat_request(model: FakeModel) -> Dict[str, Any]:
    fresh_environment(); model.reset()
    await consume("benchmark repeat", model.time_scale)
    cold_calls = model.calls; model.reset()
    t0 = time.perf_counter()
    r = await consume("benchmark repeat", model.time_scale)
    return {"cold_calls": cold_calls, "repeat_model_calls": model.calls, "repeat_tokens": _tokens(model),
            "repeat_wall_ms": (time.perf_counter() - t0) * 1000, "served_from_cache": r["cached"]}


async def concurrent_duplicates(model: FakeModel, k: int = 5) -> Dict[str, Any]:
    fresh_environment(); model.reset()
    t0 = time.perf_counter()
    results = await asyncio.gather(*[consume("benchmark concurrent", model.time_scale) for _ in range(k)])
    unique_needed = len(cg.SECTION_NAMES)
    return {"simultaneous_identical_requests": k, "unique_sections_needed": unique_needed, "model_calls": model.calls,
            "duplicate_calls": model.calls - unique_needed, **_tokens(model),
            "wasted_token_share": 1 - unique_needed / model.calls if model.calls else 0.0,
            "total_s": (time.perf_counter() - t0) / model.time_scale,
            "per_request_total_s": summarise(r["total_s"] for r in results)}


async def restart_survival(model: FakeModel) -> Dict[str, Any]:
    fresh_environment(); model.reset()
    await consume("benchmark restart", model.time_scale)
    first = model.calls; model.reset()
    fresh_environment()                       # simulates an API restart: container, cache and (in-memory) DB are rebuilt
    r = await consume("benchmark restart", model.time_scale)
    return {"calls_before_restart": first, "calls_after_restart": model.calls, "served_from_cache_after_restart": r["cached"],
            "completed_content_survives_restart": model.calls == 0}


async def partial_failure(model: FakeModel) -> Dict[str, Any]:
    fresh_environment(); model.reset(); model.fail_sections = {"flashcards"}
    first = await consume("benchmark partial", model.time_scale)
    calls_first, tokens_first = model.calls, _tokens(model)
    model.fail_sections = set(); model.reset()
    second = await consume("benchmark partial", model.time_scale, only=["flashcards"])
    return {"first_run_calls": calls_first, "first_run_incomplete": first["incomplete_sections"], "first_run_tokens": tokens_first,
            "regeneration_calls": model.calls, "regeneration_only_failed_section": model.calls == 1,
            "regeneration_total_s": second["total_s"]}
