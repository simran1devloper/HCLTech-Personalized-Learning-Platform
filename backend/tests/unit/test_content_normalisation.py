import json

import pytest

from services import content_generation as cg
from services.content_generation import (SECTIONS, container_to_markdown, normalise_recommendations, parse_section,
                                        tidy_markdown, _scalar_text)

SEC = {s.name: s for s in SECTIONS}
BAD_TOKENS = ("[object Object]", "{'", "<class", "None,", "True,")


def clean(text: str):
    for tok in BAD_TOKENS:
        assert tok not in text, (tok, text)


def test_scalar_text_never_leaks_containers():
    assert _scalar_text({"title": "Caching", "reason": "speeds reads"}) == "Caching — reason: speeds reads"
    assert _scalar_text({"area": "indexes"}) == "indexes" and _scalar_text(["a", {"name": "b"}, None]) == "a, b"
    assert _scalar_text(None) == "" and _scalar_text(3) == "3" and _scalar_text({"k": {"x": 1}}) == "k: x: 1"
    clean(_scalar_text({"a": {"b": [1, {"c": 2}]}}))


@pytest.mark.parametrize("raw", [
    json.dumps({"overview": "A short intro", "key_points": ["one", "two"], "details": {"depth": 2, "tags": ["x"]}}),
    "{'overview': 'A short intro', 'key_points': ['one', 'two'], 'flag': True, 'missing': None}",       # Python repr (single quotes, True/None)
    json.dumps([{"step": "first", "detail": "do this"}, {"step": "second", "detail": "then that"}]),
])
def test_markdown_sections_that_arrive_as_data_become_readable_markdown(raw):
    out = tidy_markdown(raw, False)
    clean(out)
    assert len(out) > 20 and ("**" in out or out.startswith("- ") or "###" in out)
    assert parse_section(SEC["deep_dive"], raw, False) == out


def test_plain_markdown_is_left_alone_and_brace_text_is_not_misread():
    md = "## Title\n\nUse `{x}` placeholders and {curly} braces in prose; a set is {1, 2} in math."
    assert tidy_markdown(md, False) == md
    assert tidy_markdown("{not valid json or python", False) == "{not valid json or python"


def test_recommendations_are_coerced_to_strings_and_title_objects():
    messy = {"weak_areas": [{"area": "joins"}, "indexes", None, ""], "practice_suggestions": ["drill", {"name": "build a mini project"}],
             "next_topics": ["Transactions", {"name": "Locking", "why": "ignored"}, {"title": "MVCC", "reason": "how reads work"}],
             "resources": [{"title": "Docs", "description": "official"}, "A book", 5]}
    r = normalise_recommendations(messy)
    assert r["weak_areas"] == ["joins", "indexes"] and r["practice_suggestions"] == ["drill", "build a mini project"]
    assert r["next_topics"] == [{"title": "Transactions", "reason": ""}, {"title": "Locking", "reason": ""}, {"title": "MVCC", "reason": "how reads work"}]
    assert r["resources"] == [{"title": "Docs", "description": "official"}, {"title": "A book", "description": ""}, {"title": "5", "description": ""}]
    assert normalise_recommendations({}) == {"weak_areas": [], "practice_suggestions": [], "next_topics": [], "resources": []}
    for v in r.values():
        clean(json.dumps(v))


def test_json_sections_accept_nested_values_without_leaking_objects():
    kc = json.dumps({"key_concepts": [{"term": {"name": "Index"}, "definition": {"text": "a lookup structure", "example": "B-tree"}}] * 3})
    out = parse_section(SEC["key_concepts"], kc, False)
    assert out[0]["term"] == "Index" and "a lookup structure" in out[0]["definition"]
    clean(json.dumps(out))
    fc = json.dumps({"flashcards": [{"question": ["What is a join?"], "answer": {"text": "Combining rows"}}] * 4})
    out = parse_section(SEC["flashcards"], fc, False)
    assert out[0] == {"question": "What is a join?", "answer": "Combining rows"}
    rec = parse_section(SEC["recommendations"], json.dumps({"weak_areas": [{"area": "x"}], "next_topics": ["y"], "practice_suggestions": ["z"], "resources": []}), False)
    assert rec["weak_areas"] == ["x"] and rec["next_topics"] == [{"title": "y", "reason": ""}]


def test_container_to_markdown_structure():
    md = container_to_markdown({"summary_text": "hi", "points": ["a", "b"], "nested": {"k": "v"}})
    assert "**Summary text:** hi" in md and "- a" in md and "**K:** v" in md


async def test_end_to_end_a_section_returned_as_a_dict_is_served_as_markdown():
    async def call(sec, prompt): return json.dumps({"overview": "text " * 20, "steps": ["one", "two"]}), False
    value, ok = await cg.generate_section(SEC["practical_guide"], "Python", "Beginner", 3, call)
    assert ok and "**Overview:**" in value and "[object" not in value and "{'" not in value
