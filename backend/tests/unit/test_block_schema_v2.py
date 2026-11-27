import pytest
from pydantic import ValidationError

from app.domains.content_engine.schema import (BLOCK_TYPES, SCHEMA_VERSION, SectionContent, assign_ids, legacy_to_v2,
                                               make_block_id, validate_block)

VALID = {
    "heading": {"level": 2, "text": "Overview"},
    "text": {"markdown": "Plain **markdown** with `code`."},
    "list": {"ordered": True, "items": ["one", "two"]},
    "table": {"headers": ["a", "b"], "rows": [["1", "2"]], "caption": "c"},
    "callout": {"variant": "warning", "text": "Careful here."},
    "mermaid": {"source": "flowchart TD\n A[Start] --> B[End]"},
    "math": {"latex": r"\frac{a}{b}", "display": True, "variables": [{"symbol": "a", "meaning": "numerator"}]},
    "chart": {"chart_type": "bar", "categories": ["a", "b"], "series": [{"name": "s", "data": [1, 2]}], "source": "benchmark"},
    "image": {"url": "https://example.com/a.png", "alt": "A diagram of the flow"},
    "code": {"language": "python", "code": "print('<b>ok</b>')"},
    "algorithm": {"name": "Binary search", "steps": ["pick mid", "compare"], "time_complexity": "O(log n)"},
    "quiz": {"question_ids": ["q1"]},
    "flashcard": {"front": "Q?", "back": "A."},
    "exercise": {"prompt": "Do it", "hints": ["h"], "difficulty": 3},
    "citation": {"label": "[1]", "source_title": "Docs", "url": "https://docs.python.org/3/", "quote": "short quote"},
}


def mk(t, **over):
    return {"id": f"blk-{t}", "type": t, **VALID[t], **over}


def test_every_block_type_is_covered_and_valid():
    assert set(VALID) == set(BLOCK_TYPES) and len(BLOCK_TYPES) == 15
    for t in BLOCK_TYPES:
        b = validate_block(mk(t))
        assert b.type == t and b.id == f"blk-{t}"


@pytest.mark.parametrize("t,bad", [
    ("heading", {"level": 9}), ("heading", {"text": ""}), ("text", {"markdown": ""}), ("list", {"items": []}),
    ("list", {"items": [" "]}), ("table", {"rows": [["1"]]}), ("callout", {"variant": "loud"}),
    ("mermaid", {"source": "not a diagram"}), ("math", {"latex": r"\href{javascript:alert(1)}{x}"}),
    ("math", {"latex": r"\frac{a}{b"}), ("chart", {"series": [{"name": "s", "data": [1]}]}), ("chart", {"categories": ["a"]}),
    ("image", {"url": "http://example.com/a.png"}), ("image", {"url": "https://u:p@example.com/a.png"}), ("image", {"alt": "x"}),
    ("code", {"language": "py thon"}), ("algorithm", {"steps": []}), ("quiz", {"question_ids": []}),
    ("flashcard", {"back": ""}), ("exercise", {"difficulty": 9}), ("citation", {"url": "javascript:alert(1)"}),
    ("citation", {"quote": "x" * 301}), ("citation", {"label": ""}),
])
def test_invalid_blocks_are_rejected(t, bad):
    with pytest.raises(ValidationError):
        validate_block(mk(t, **bad))


@pytest.mark.parametrize("payload", ["<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "[x](javascript:alert(1))", "<iframe src=//x>"])
def test_unsafe_markup_is_rejected_in_text_fields_but_code_is_literal(payload):
    for t, field in [("text", "markdown"), ("callout", "text"), ("heading", "text"), ("flashcard", "front"), ("exercise", "prompt")]:
        with pytest.raises(ValidationError):
            validate_block(mk(t, **{field: payload}))
    with pytest.raises(ValidationError):
        validate_block(mk("list", items=["fine", payload]))
    validate_block(mk("code", code=payload))                          # rendered as text only
    validate_block(mk("algorithm", pseudocode=payload))


def test_id_rules_and_unknown_type():
    for bad in ["", "ab", "has space", "x" * 65, "-lead"]:
        with pytest.raises(ValidationError):
            validate_block({**mk("text"), "id": bad})
    with pytest.raises(ValidationError):
        validate_block({"id": "blk-1", "type": "video", "url": "https://x"})
    with pytest.raises(ValidationError):
        validate_block({"type": "text", "markdown": "no id"})


def test_unknown_fields_are_dropped_not_stored():
    b = validate_block({**mk("text"), "evil": "<script>", "onclick": "x"})
    assert "evil" not in b.model_dump() and "onclick" not in b.model_dump()


def test_ids_are_deterministic_position_and_content_dependent():
    raw = {"type": "text", "markdown": "hello"}
    a, b = make_block_id("deep_dive", 3, raw), make_block_id("deep_dive", 3, dict(raw))
    assert a == b and a.startswith("b_003_")
    assert make_block_id("deep_dive", 4, raw) != a and make_block_id("summary", 3, raw) != a
    assert make_block_id("deep_dive", 3, {**raw, "markdown": "other"}) != a
    out = assign_ids("s", [raw, {**raw, "id": "keep-me"}])
    assert out[0]["id"].startswith("b_000_") and out[1]["id"] == "keep-me"
    assert assign_ids("s", [raw]) == assign_ids("s", [raw])           # idempotent regeneration keeps identity
    validate_block(out[0])                                            # generated ids satisfy the schema


def test_section_content_requires_unique_ids_and_carries_schema_version():
    s = SectionContent(section="summary", blocks=[mk("text"), mk("heading")])
    assert s.schema_version == SCHEMA_VERSION == "2.0" and len(s.blocks) == 2
    with pytest.raises(ValidationError):
        SectionContent(section="summary", blocks=[mk("text"), mk("text")])
    with pytest.raises(ValidationError):
        SectionContent(section="summary", blocks=[mk("text", id=f"blk-{i:03d}") for i in range(201)])


def test_legacy_v1_blocks_map_to_valid_v2_blocks():
    legacy = [
        {"type": "text", "markdown": "hi there"}, {"type": "code", "language": "python", "code": "x=1"},
        {"type": "table", "headers": ["a"], "rows": [["1"]]}, {"type": "mermaid", "source": "flowchart TD\n A-->B"},
        {"type": "math", "latex": "x^2"}, {"type": "quiz", "question_ids": ["q"]}, {"type": "exercise", "prompt": "p"},
        {"type": "flashcards", "cards": [{"front": "f1", "back": "b1"}, {"front": "f2", "back": "b2"}]},
        {"type": "case_study", "title": "Outage", "scenario": "Something broke.", "questions": ["Why?"]},
        {"type": "tutorial", "steps": ["one", "two"]},
    ]
    converted = [nb for blk in legacy for nb in legacy_to_v2("legacy", blk)]
    assert [b["type"] for b in converted].count("flashcard") == 2 and "case_study" not in {b["type"] for b in converted}
    section = SectionContent(section="legacy", blocks=assign_ids("legacy", converted))
    assert len(section.blocks) == len(converted)
    with pytest.raises(ValueError):
        legacy_to_v2("legacy", {"type": "video"})
