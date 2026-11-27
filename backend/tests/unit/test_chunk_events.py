"""PR4 of content-generation v2: live chunks, progress detail, icons, heartbeats."""
import asyncio
import json
import random

import pytest

from app.domains.content_engine.streaming import ArrayItemScanner, MarkdownChunker
from services import content_generation as cg
from services import prompt_registry as pr
from services.pipeline import Tracker, stage_list, with_heartbeats
from tests.unit.test_stream_watchdog import Streamer, router, use_router

SEC = {s.name: s for s in cg.SECTIONS}


def splits(text, rnd, max_len=7):
    i, out = 0, []
    while i < len(text):
        n = rnd.randint(1, max_len)
        out.append(text[i:i + n]); i += n
    return out


# ------------------------------------------------------------------ markdown chunker
MD = ("## Title\n\nFirst paragraph with `code` and more words.\n\n```mermaid\nflowchart TD\n    A[\"x\"] --> B[\"y\"]\n\n    B --> C[\"z\"]\n```\n\n"
      "```python\nprint('hi')\n```\n\nLast line without newline")


@pytest.mark.parametrize("seed", range(25))
def test_chunker_never_releases_an_open_fence_and_loses_nothing(seed):
    c, got, seen = MarkdownChunker(), [], ""
    for tok in splits(MD, random.Random(seed)):
        if (d := c.feed(tok)):
            got.append(d); seen += d
            assert seen.count("```") % 2 == 0, f"released inside an open fence: {seen!r}"
    if (d := c.flush()):
        got.append(d)
    assert "".join(got) == MD


def test_long_paragraph_without_newlines_is_released_at_a_word_boundary():
    c, out = MarkdownChunker(soft_limit=30), []
    for w in ("word " * 20).split(" "):
        if (d := c.feed(w + " ")):
            out.append(d)
    assert out and all(d.endswith(" ") for d in out)


def test_unclosed_fence_is_held_back_until_flush():
    c = MarkdownChunker(soft_limit=10)
    assert c.feed("```mermaid\nflowchart TD\n A --> B and a long enough line to pass the soft limit\n") is None
    assert c.flush().startswith("```mermaid")


# ------------------------------------------------------------------ item scanner
CARDS = {"flashcards": [{"question": "Why \"quoted\" [x]?", "answer": "Because {braces} and \\ backslash"}, {"question": "Q2", "answer": "A2"}, {"question": "Q3", "answer": "A3"}]}


@pytest.mark.parametrize("payload", [CARDS, CARDS["flashcards"]])
@pytest.mark.parametrize("seed", range(25))
def test_scanner_yields_each_item_once_for_any_token_split(payload, seed):
    text = "Sure, here you go:\n" + json.dumps(payload, indent=2) if isinstance(payload, list) else json.dumps(payload)
    s, got = ArrayItemScanner(), []
    for tok in splits(text, random.Random(seed), 9):
        got += s.feed(tok)
    assert got == CARDS["flashcards"]


def test_scanner_ignores_text_after_the_array_and_partial_items():
    s = ArrayItemScanner()
    assert s.feed('{"k": [{"a": 1}, {"b": ') == [{"a": 1}]
    assert s.feed('2}], "other": [{"c": 3}]}') == [{"b": 2}]


# ------------------------------------------------------------------ pipeline events
async def run(monkeypatch, provider, names, **settings):
    use_router(monkeypatch, router(provider), CONTENT_STREAMING=True, **settings)
    return [ev async for ev in cg.run_pipeline("Rust", "Beginner", 5, only=names, concurrency=2)]


def tokens(text, n=6):
    return [text[i:i + n] for i in range(0, len(text), n)]


async def test_flashcards_stream_item_by_item_then_the_validated_section(monkeypatch):
    cards = {"flashcards": [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(4)] + [{"question": "", "answer": "x"}]}
    events = await run(monkeypatch, Streamer("ollama", tokens(json.dumps(cards))), ["flashcards"])
    chunks = [e["data"] for e in events if e["event"] == "chunk"]
    assert [c["item"] for c in chunks] == cards["flashcards"][:4]                    # invalid item skipped
    assert [c["seq"] for c in chunks] == [0, 1, 2, 3] and {c["kind"] for c in chunks} == {"item"} and {c["attempt"] for c in chunks} == {1}
    kinds = [e["event"] for e in events]
    assert kinds.index("section") > max(i for i, k in enumerate(kinds) if k == "chunk")   # chunks first, validated section last
    sec = next(e["data"] for e in events if e["event"] == "section")
    assert sec["ok"] and len(sec["value"]) == 4


async def test_markdown_streams_blocks_that_add_up_to_the_section(monkeypatch):
    body = "## Overview\n\n" + "A solid sentence about the topic. " * 12 + "\n\n```mermaid\nflowchart TD\n    A[\"a\"] --> B[\"b\"]\n```\n\nThe end of the section."
    events = await run(monkeypatch, Streamer("ollama", tokens(body, 5)), ["deep_dive"])
    chunks = [e["data"] for e in events if e["event"] == "chunk"]
    assert len(chunks) >= 2 and all(c["kind"] == "md" for c in chunks)
    streamed = ""
    for c in chunks:
        streamed += c["delta"]
        assert streamed.count("```") % 2 == 0
    assert body.startswith(streamed)
    assert next(e["data"] for e in events if e["event"] == "section")["value"].startswith("## Overview")


async def test_progress_detail_reports_real_words_while_streaming(monkeypatch):
    ticks = iter(range(0, 1000))
    monkeypatch.setattr(cg, "_clock", lambda: next(ticks) * 1.1)               # every token is >1 s after the last report
    body = "## Summary\n\n" + "word " * 60
    events = await run(monkeypatch, Streamer("ollama", tokens(body, 10)), ["summary"])
    details = [e["data"].get("detail", "") for e in events if e["event"] == "stage" and e["data"]["status"] == "running"]
    assert "The model started answering…" in details and any(d.startswith("Writing… ") and d.endswith("words so far") for d in details)


async def test_retry_restarts_the_preview_with_a_new_attempt_number(monkeypatch):
    good = json.dumps({"flashcards": [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(3)]})
    bad = json.dumps({"flashcards": [{"question": "only", "answer": "one"}]})
    p = Streamer("ollama", [])
    scripts = iter([tokens(bad), tokens(good)])
    orig = p.generate_stream

    async def gen(prompt, params=None):
        p.script = next(scripts)
        async for t in orig(prompt, params) if params else orig(prompt):
            yield t
    p.generate_stream = gen
    events = await run(monkeypatch, p, ["flashcards"])
    chunks = [e["data"] for e in events if e["event"] == "chunk"]
    assert [c["attempt"] for c in chunks] == [1, 2, 2, 2] and [c["seq"] for c in chunks][1:] == [0, 1, 2]
    assert next(e["data"] for e in events if e["event"] == "section")["ok"]


async def test_fake_call_functions_still_work_and_emit_no_chunks():
    async def call(sec, prompt):
        return "## Summary\n\n" + "text " * 30, False
    events = [e async for e in cg.run_pipeline("Rust", "Beginner", 5, only=["summary"], call=call)]
    assert [e["event"] for e in events].count("chunk") == 0 and any(e["event"] == "section" and e["data"]["ok"] for e in events)


# ------------------------------------------------------------------ icons
def test_every_section_has_a_deterministic_lucide_icon_and_meta_stages_carry_it():
    assert set(pr.SECTION_ICONS) == set(pr.SECTION_PROMPTS)
    assert all(v.replace("-", "").isalpha() and v == v.lower() for v in pr.SECTION_ICONS.values())
    stages = stage_list(("plan", "Plan"), ("section:summary", "Write summary", pr.section_icon("summary")))
    assert "icon" not in stages[0] and stages[1]["icon"] == "file-text" and stages[1]["status"] == "pending"


# ------------------------------------------------------------------ heartbeats
async def test_heartbeat_yields_none_during_silence_and_passes_events_through():
    async def src():
        yield 1
        await asyncio.sleep(0.12)
        yield 2
    out = [x async for x in with_heartbeats(src(), interval=0.03)]
    assert [x for x in out if x is not None] == [1, 2] and out.count(None) >= 2


async def test_closing_the_heartbeat_wrapper_cancels_the_source():
    cancelled = asyncio.Event()

    async def src():
        try:
            yield 1
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set(); raise
    it = with_heartbeats(src(), interval=0.02)
    assert await it.__anext__() == 1
    await it.__anext__()   # a heartbeat
    await it.aclose()
    assert cancelled.is_set()
