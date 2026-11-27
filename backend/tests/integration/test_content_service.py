import asyncio

import pytest

from app.domains.content_engine.models import CourseSpec
from app.domains.content_engine.service import ContentService, SectionResult
from app.infrastructure.database.content_store import ContentStore
from app.infrastructure.database.jobs import JobRepository

SPEC = CourseSpec("Binary Search", "Beginner")


def make(db, producer):
    return ContentService(ContentStore(db), JobRepository(db), producer, lambda s: "1.0.0", poll_s=0.01)


def test_concurrent_requests_share_one_model_call_and_a_second_request_is_a_cache_hit(db):
    calls = []

    async def producer(section, spec):
        calls.append(section)
        await asyncio.sleep(0.2)
        return SectionResult("## hi", True, 10, 20, 5.0, "m", "p")

    svc = make(db, producer)

    async def go():
        return await asyncio.gather(*[svc.get_section(SPEC, "summary", "u1") for _ in range(8)])

    results = asyncio.run(go())
    assert len(calls) == 1 and all(r and r.payload == "## hi" for r in results)
    assert asyncio.run(svc.get_section(SPEC, "summary", "u2")).payload == "## hi" and len(calls) == 1
    totals = svc.jobs.usage_totals()
    assert totals["calls"] == 1 and totals["tokens_in"] == 10 and totals["tokens_out"] == 20


def test_failure_is_recorded_then_retry_succeeds_once(db):
    state = {"n": 0}

    async def producer(section, spec):
        state["n"] += 1
        return SectionResult(["a"] if state["n"] > 1 else None, state["n"] > 1, 1, 1, 1.0, "m", "p")

    svc = make(db, producer)
    assert asyncio.run(svc.get_section(SPEC, "key_concepts", "u1")) is None
    assert svc.status(SPEC)["sections"][1]["status"] == "FAILED"
    assert asyncio.run(svc.get_section(SPEC, "key_concepts", "u1")).payload == ["a"]
    assert state["n"] == 2


def test_legacy_alias_and_unknown_section(db):
    async def producer(section, spec):
        return SectionResult(f"x:{section}", True)

    svc = make(db, producer)
    assert asyncio.run(svc.get_section(SPEC, "viva_voice")).section_id == "interview_qa"
    with pytest.raises(KeyError):
        asyncio.run(svc.get_section(SPEC, "nope"))
