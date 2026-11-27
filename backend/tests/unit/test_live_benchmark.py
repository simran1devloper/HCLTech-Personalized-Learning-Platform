import json

import pytest

from benchmarks import live_streaming as lb
from services import content_generation as cg


async def fake_call(sec, prompt):
    good = {"key_concepts": {"key_concepts": [{"term": f"T{i}", "definition": f"D{i}"} for i in range(4)]},
            "flashcards": {"flashcards": [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(4)]},
            "recommendations": {"weak_areas": ["w"], "next_topics": [], "practice_suggestions": [], "resources": []}}
    return (json.dumps(good[sec.name]) if sec.name in good else "## T\n\n" + "words " * 30), False


async def test_one_run_collects_per_section_outcomes(monkeypatch):
    monkeypatch.setattr(cg, "_call_model", fake_call)
    r = await lb.one_run("Rust", "Beginner", 5)
    assert r["sections"] == 10 and r["sections_ok"] == 10 and r["failed_sections"] == [] and r["retried_sections"] == []
    assert r["first_section_s"] is not None and r["first_chunk_s"] is None   # fakes bypass streaming: no chunks


def test_summary_and_markdown_handle_missing_values():
    runs = [{"total_s": 10.0, "first_chunk_s": 1.0, "first_section_s": 3.0, "sections_ok": 9, "sections": 10, "attempts": {}, "retried_sections": ["flashcards"], "failed_sections": ["deep_dive"], "tokens_out": 0},
            {"total_s": 20.0, "first_chunk_s": None, "first_section_s": 5.0, "sections_ok": 10, "sections": 10, "attempts": {}, "retried_sections": [], "failed_sections": [], "tokens_out": 0}]
    s = lb.summarise(runs)
    assert s["section_success_rate"] == 0.95 and s["guides_fully_complete"] == 1 and s["retried_sections_per_guide"] == 0.5 and s["total_s"]["mean"] == 15.0
    md = lb.to_markdown({"label": "x", "date": "d", "topic": "t", "level": "l", "providers": "p", "model": "m", "flags": {}}, s, runs)
    assert "95.0%" in md and "n/a" not in md.split("| Run |")[0].split("Time to first chunk")[0]
