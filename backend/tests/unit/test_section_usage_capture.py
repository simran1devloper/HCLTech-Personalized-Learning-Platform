import asyncio

import pytest

from app.core.exceptions import ProviderError
from app.observability.metrics import metrics
from services import content_generation as cg
from services.pipeline import Tracker
from tests.unit.test_content_generation import good_output


@pytest.fixture(autouse=True)
def _reset():
    metrics.reset()


def result(sec, tin, tout, ms=120.0):
    return cg.CallResult(good_output(sec), False, tin, tout, ms, "fake-model", "fake")


async def test_usage_counters_equal_provider_reported_usage_per_section():
    expected = {"in": 0, "out": 0}

    async def call(sec, prompt):
        tin, tout = 100 + len(sec.name), 50 + len(sec.name)
        expected["in"] += tin; expected["out"] += tout
        return result(sec, tin, tout)
    got = [n async for n, _, _ in cg.iter_sections("Python", "Beginner", 3, call=call)]
    assert sorted(got) == sorted(cg.SECTION_NAMES)
    total_in = sum(metrics.get("content_tokens_in_total", section=n) for n in cg.SECTION_NAMES)
    total_out = sum(metrics.get("content_tokens_out_total", section=n) for n in cg.SECTION_NAMES)
    assert (total_in, total_out) == (expected["in"], expected["out"])
    assert metrics.get("content_section_calls_total", section="summary", outcome="ok") == 1
    assert 'content_section_latency_ms_count{section="summary"} 1' in metrics.render()


async def test_stage_done_event_carries_tokens():
    q = asyncio.Queue(); t = Tracker(q)

    async def call(sec, prompt): return result(sec, 321, 654)
    await cg.generate_section(cg.SECTIONS[0], "Python", "Beginner", 3, call, tracker=t)
    done = [q.get_nowait()["data"] for _ in range(q.qsize())][-1]
    assert done["status"] == "done" and done["tokens_in"] == 321 and done["tokens_out"] == 654


async def test_legacy_tuple_fakes_still_work_and_record_zero_usage():
    async def call(sec, prompt): return good_output(sec), False
    value, ok = await cg.generate_section(cg.SECTIONS[7], "Python", "Beginner", 3, call)
    assert ok and metrics.get("content_tokens_in_total", section="flashcards") == 0
    assert metrics.get("content_section_calls_total", section="flashcards", outcome="ok") == 1


async def test_failed_calls_are_counted_and_retried_calls_accumulate_usage():
    calls = []

    async def call(sec, prompt):
        calls.append(1)
        if len(calls) == 1:
            raise ProviderError("down")
        return result(sec, 10, 20)
    value, ok = await cg.generate_section(cg.SECTIONS[7], "Python", "Beginner", 3, call)
    assert ok
    assert metrics.get("content_section_calls_total", section="flashcards", outcome="provider_error") == 1
    assert metrics.get("content_tokens_in_total", section="flashcards") == 10   # only the successful call reported usage
