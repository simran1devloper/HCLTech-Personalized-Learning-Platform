import asyncio
import math

import pytest

from benchmarks import scenarios as sc
from benchmarks.fake_model import FakeModel
from benchmarks.report import to_markdown
from benchmarks.stats import percentile, relative_spread, summarise
from services import content_generation as cg


# ---------------- statistics ----------------
def test_percentile_matches_known_values():
    xs = [15, 20, 35, 40, 50]
    assert percentile(xs, 0) == 15 and percentile(xs, 100) == 50 and percentile(xs, 50) == 35
    assert percentile(xs, 25) == 20 and percentile(xs, 95) == pytest.approx(48.0)
    assert percentile([7], 95) == 7 and percentile([1, 2], 50) == pytest.approx(1.5)
    assert percentile(list(range(1, 101)), 95) == pytest.approx(95.05)


def test_percentile_rejects_bad_input():
    for args in [([], 50), ([1], -1), ([1], 101)]:
        with pytest.raises(ValueError):
            percentile(*args)


def test_summarise_and_spread():
    s = summarise([1, 2, 3, 4, 100])
    assert s["n"] == 5 and s["p50"] == 3 and s["max"] == 100 and s["mean"] == pytest.approx(22.0)
    assert summarise([]) == {"n": 0}
    assert relative_spread(100, 90) == pytest.approx(0.1) and relative_spread(0, 0) == 0


# ---------------- fake model ----------------
async def test_fake_model_is_deterministic_and_reports_exact_output_tokens():
    m = FakeModel(time_scale=0.0001)
    sec = cg.SECTIONS[2]   # deep_dive, budget 1600
    a = await m(sec, "prompt text"); b = await m(sec, "prompt text")
    assert a == b and a.tokens_out == int(1600 * 0.75) and a.tokens_in == len("prompt text") // 4
    assert m.calls == 2 and m.ledger[0]["simulated_s"] == pytest.approx(2.0 + 1200 / 7.0)


async def test_every_fake_section_output_parses_with_the_real_parser():
    m = FakeModel(time_scale=0.0001)
    for sec in cg.SECTIONS:
        res = await m(sec, "p")
        assert cg.parse_section(sec, res.text, res.truncated) is not None, sec.name


async def test_server_slots_limit_parallelism():
    m = FakeModel(time_scale=0.002, server_slots=1, ttft_s=1.0, throughput_tps=1000.0)
    import time
    t0 = time.perf_counter()
    await asyncio.gather(*[m(cg.SECTIONS[9], "p") for _ in range(4)])
    serial = time.perf_counter() - t0
    m2 = FakeModel(time_scale=0.002, server_slots=4, ttft_s=1.0, throughput_tps=1000.0)
    t0 = time.perf_counter()
    await asyncio.gather(*[m2(cg.SECTIONS[9], "p") for _ in range(4)])
    assert serial > 2.5 * (time.perf_counter() - t0)


# ---------------- scenarios (small scale) ----------------
@pytest.fixture
def model(monkeypatch):
    m = FakeModel(time_scale=0.0005, server_slots=2)
    monkeypatch.setattr(cg, "_call_model", m)
    return m


async def test_cold_generation_usage_matches_provider_ledger_and_is_reproducible(model):
    a = await sc.cold_generation(model, reps=2)
    b = await sc.cold_generation(model, reps=2)
    sa, sb = a["summary"], b["summary"]
    assert sa["model_calls"] == len(cg.SECTION_NAMES) and sa["usage_matches_ledger"] is True
    assert (sa["tokens_in"], sa["tokens_out"]) == (sb["tokens_in"], sb["tokens_out"])        # exactly reproducible
    assert relative_spread(sa["total_s"]["p50"], sb["total_s"]["p50"]) < 0.25                # timing within tolerance
    assert sa["time_to_first_section_s"]["p50"] < sa["total_s"]["p50"]


async def test_repeat_request_is_served_from_cache_with_zero_model_calls(model):
    r = await sc.repeat_request(model)
    assert r["repeat_model_calls"] == 0 and r["served_from_cache"] is True and r["cold_calls"] == len(cg.SECTION_NAMES)


async def test_concurrent_and_restart_and_partial_scenarios_report_consistent_facts(model):
    con = await sc.concurrent_duplicates(model, k=3)
    assert con["unique_sections_needed"] == 10 and con["model_calls"] >= 10
    assert con["duplicate_calls"] == con["model_calls"] - 10 and 0 <= con["wasted_token_share"] < 1
    rst = await sc.restart_survival(model)
    assert rst["calls_before_restart"] == 10 and rst["completed_content_survives_restart"] == (rst["calls_after_restart"] == 0)
    par = await sc.partial_failure(model)
    assert par["first_run_incomplete"] == ["flashcards"] and par["regeneration_only_failed_section"] is True


async def test_report_renders_all_sections(model):
    from benchmarks.run_baseline import run
    result = await run("fake", 1, model, "test")
    md = to_markdown(result)
    for h in ("Cold generation", "Identical repeat request", "simultaneous identical requests", "restart", "regenerated"):
        assert h in md
    assert math.isfinite(result["scenarios"]["cold_generation"]["summary"]["total_s"]["p95"])
