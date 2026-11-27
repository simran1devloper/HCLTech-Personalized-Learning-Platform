"""Time-budgeted generation: a slow, memory-starved model must finish a *useful* guide on time instead of timing out."""
import asyncio

import pytest

from app.core.config import AppSettings, get_settings
from app.core.exceptions import ProviderStallError
from services import content_budget as cb
from services import content_generation as cg
from services import prompt_registry as pr
from services.pipeline import Tracker

ALL = [cb.Item(s.name, s.kind, s.max_tokens) for s in cg.SECTIONS]
MARKDOWN_CORE = [n for n in pr.CORE_SECTIONS if pr.SECTION_PROMPTS[n].kind == "markdown"]


@pytest.fixture(autouse=True)
def fresh_speeds():
    cb.reset_speeds()
    yield
    cb.reset_speeds()


# ------------------------------------------------------------------ planner
def test_unknown_speed_or_fast_model_means_full_length_everywhere():
    full = {i.name: i.max_tokens for i in ALL}
    assert cb.plan_tokens(ALL, 600, None) == full
    assert cb.plan_tokens(ALL, 600, 60.0) == full                      # cloud-like speed: 9,500 tokens in well under a minute


def test_medium_speed_keeps_core_sections_whole_and_defers_optional_ones_in_priority_order():
    # enough for the core guide (~3.2k expected tokens) plus roughly one optional section
    plan = cb.plan_tokens(ALL, 600, 8.0)
    assert all(plan[n] == pr.SECTION_PROMPTS[n].max_tokens for n in pr.CORE_SECTIONS)
    deferred = [n for n in pr.OPTIONAL_SECTIONS if plan[n] is None]
    kept = [n for n in pr.OPTIONAL_SECTIONS if plan[n] is not None]
    assert deferred and kept and pr.OPTIONAL_SECTIONS.index(kept[-1]) < pr.OPTIONAL_SECTIONS.index(deferred[0])   # a prefix is kept


def test_slow_model_keeps_the_essentials_shortens_markdown_and_never_shrinks_json():
    plan = cb.plan_tokens(ALL, 600, 4.0)                                # a CPU-only 8B model: ~2,000 tokens of budget
    assert all(plan[n] is None for n in pr.OPTIONAL_SECTIONS)
    for n in ("summary", "key_concepts", "flashcards", "deep_dive"):
        assert plan[n] is not None, n                                    # the essentials survive
    for n in ("key_concepts", "flashcards"):
        assert plan[n] == pr.SECTION_PROMPTS[n].max_tokens              # a cut-off JSON list is invalid, so it is never shrunk
    for n in ("summary", "deep_dive"):
        assert pr.MIN_TOKENS[n] <= plan[n] <= pr.SECTION_PROMPTS[n].max_tokens
    assert plan["deep_dive"] < pr.SECTION_PROMPTS["deep_dive"].max_tokens
    assert plan["agent_intro"] is None                                  # lowest-priority core section goes first
    assert sum(cb._expected(v) for v in plan.values() if v) <= 600 * 4.0 * cb.SAFETY * 1.2   # fits the budget (floors may add slack)


def test_very_slow_model_defers_lowest_priority_core_sections_but_keeps_the_summary():
    plan = cb.plan_tokens(ALL, 600, 1.0)
    assert plan["summary"] is not None and plan["deep_dive"] is None
    assert all(plan[n] is None for n in pr.OPTIONAL_SECTIONS)


def test_no_time_left_still_returns_a_plan_and_a_single_section_is_never_dropped():
    assert cb.plan_tokens([cb.Item("deep_dive", "markdown", 1600)], 0, 4.0)["deep_dive"] == pr.MIN_TOKENS["deep_dive"]
    assert cb.plan_tokens(ALL, -50, 4.0)["summary"] is not None


def test_speed_tracking_ignores_meaningless_samples_and_smooths_the_rest():
    assert cb.record_speed("m", 10, 5) is None and cb.record_speed("m", 500, 0.1) is None     # too small / too fast to trust
    assert cb.record_speed("m", 400, 100) == pytest.approx(4.0)
    assert cb.record_speed("m", 800, 100) == pytest.approx(6.0)                               # EMA: 0.5*8 + 0.5*4
    assert cb.speed("m") == pytest.approx(6.0) and cb.speed("other") is None


# ------------------------------------------------------------------ pipeline
def slow_model(seen, clock, seconds=100.0):
    """Fake provider that reports 400 tokens in `seconds` (default 100 s = 4 tokens/s) per section and spends that simulated time."""
    async def call(sec, prompt):
        seen[sec.name] = (sec.max_tokens, prompt)
        clock[0] += seconds
        text = {"key_concepts": '{"key_concepts": [%s]}' % ",".join('{"term":"T%d","definition":"D%d"}' % (i, i) for i in range(4)),
                "flashcards": '{"flashcards": [%s]}' % ",".join('{"question":"Q%d","answer":"A%d"}' % (i, i) for i in range(4)),
                "recommendations": '{"weak_areas": ["w"], "next_topics": [], "practice_suggestions": [], "resources": []}'}.get(
            sec.name, "## Section\n\n" + "A reasonably long sentence about the topic. " * 8)
        return cg.CallResult(text, False, 10, 400, seconds * 1000.0, "m", "ollama")
    return call


async def run_all(monkeypatch, budget, seconds=100.0):
    seen = {}
    monkeypatch.setattr(get_settings(), "CONTENT_TIME_BUDGET_S", budget, raising=False)
    monkeypatch.setattr(cg, "_speed_key", lambda: "m")
    clock = [0.0]
    monkeypatch.setattr(cg, "_clock", lambda: clock[0])
    events = [e async for e in cg.run_pipeline("Rust", "Beginner", 5, concurrency=1, call=slow_model(seen, clock, seconds))]
    return seen, events


async def test_slow_model_finishes_a_useful_guide_and_defers_the_rest(monkeypatch):
    seen, events = await run_all(monkeypatch, 600)
    sections = {e["data"]["name"]: e["data"] for e in events if e["event"] == "section"}
    assert set(sections) == set(cg.SECTION_NAMES)                        # every section is accounted for
    for n in ("summary", "key_concepts", "flashcards", "recommendations", "agent_intro"):
        assert sections[n]["ok"] and not sections[n].get("skipped")
    for n in pr.OPTIONAL_SECTIONS:                                       # deferred, flagged, and the model was never called for them
        assert sections[n]["skipped"] and not sections[n]["ok"] and n not in seen
    skipped_stage = [e["data"] for e in events if e["event"] == "stage" and e["data"]["status"] == "skipped"]
    assert {s["id"] for s in skipped_stage} == {f"section:{n}" for n in pr.OPTIONAL_SECTIONS} and "on demand" in skipped_stage[0]["detail"]


async def test_first_section_runs_at_full_size_then_later_ones_are_sized_from_measured_speed(monkeypatch):
    seen, events = await run_all(monkeypatch, 600)
    assert seen["summary"][0] == pr.SECTION_PROMPTS["summary"].max_tokens          # speed unknown yet: nothing to plan with
    assert seen["deep_dive"][0] < pr.SECTION_PROMPTS["deep_dive"].max_tokens        # measured 4 tok/s: shortened
    assert "Length: about" in seen["deep_dive"][1] and "Length: about" not in seen["summary"][1]
    assert next(e["data"] for e in events if e["event"] == "section" and e["data"]["name"] == "deep_dive").get("shortened") is True


async def test_budget_zero_disables_planning(monkeypatch):
    seen, events = await run_all(monkeypatch, 0)
    assert set(seen) == set(cg.SECTION_NAMES) and all(seen[n][0] == pr.SECTION_PROMPTS[n].max_tokens for n in seen)


async def test_a_fast_model_is_never_cut_down(monkeypatch):
    cb.record_speed("m", 6000, 10)                                       # 600 tok/s seed from an earlier guide
    seen, _ = await run_all(monkeypatch, 600, seconds=1.0)               # and the model really is fast: 400 tokens per second
    assert set(seen) == set(cg.SECTION_NAMES) and seen["deep_dive"][0] == pr.SECTION_PROMPTS["deep_dive"].max_tokens


# ------------------------------------------------------------------ user-facing wording + defaults
async def test_a_stalled_model_is_reported_as_too_slow_not_as_a_dead_service():
    q = asyncio.Queue()

    async def call(sec, prompt):
        raise ProviderStallError("ollama sent no first token before the timeout")
    _, ok = await cg.generate_section(cg.SECTIONS[1], "Rust", "Beginner", 3, call, tracker=Tracker(q))
    stages = []
    while not q.empty():
        stages.append(q.get_nowait()["data"])
    assert not ok
    assert any("too slow" in s.get("detail", "") for s in stages if s.get("status") in ("retrying", "failed"))
    assert not any("did not respond" in s.get("detail", "") for s in stages)


def test_safe_defaults_for_a_single_cpu_ollama():
    f = AppSettings.model_fields
    assert f["CONTENT_SECTION_CONCURRENCY"].default == 1                 # Ollama serves one request at a time by default
    assert f["OLLAMA_NUM_CTX"].default == 4096 and f["CONTENT_TIME_BUDGET_S"].default > 0
