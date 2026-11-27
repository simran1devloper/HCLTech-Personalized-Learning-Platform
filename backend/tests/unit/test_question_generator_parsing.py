"""Parsing of real local-model output: Ollama's JSON mode returns ONE object (not an array), and small models mis-key the answer."""
import asyncio
import json

import pytest

from app.domains.assessments.bank import generator as gen

OPTS = ["Object.isNull(Object obj)", "obj.isnull()", "null.equals(obj)", "obj == null"]


def q(**kw):
    base = {"concept": "Java Basics", "difficulty": 2, "question_type": "single_choice", "cognitive_level": "understand", "question_text": "Which expression checks whether an object is null in Java?",
            "code_snippet": None, "options": OPTS, "correct": [3], "answer": "obj == null", "explanation": "The == operator compares the reference with null."}
    return {**base, **kw}


def run(payload, n=1):
    async def call(prompt):
        return (payload if isinstance(payload, str) else json.dumps(payload)), 10, 20
    return asyncio.run(gen.generate("Java", n, call=call))


@pytest.mark.parametrize("payload", [{"questions": [q()]}, q(), [q()], {"items": [q()]}, "Here you go:\n```json\n" + json.dumps({"questions": [q()]}) + "\n```"])
def test_every_json_shape_a_model_may_return_is_accepted(payload):
    r = run(payload)
    assert len(r["valid"]) == 1 and r["rejected"] == [] and r["valid"][0]["correct"] == [3]


@pytest.mark.parametrize("payload", ["not json", "{}", "[]", '{"questions": "none"}', "null", '{"a": 1}'])
def test_unusable_output_yields_nothing_and_never_raises(payload):
    r = run(payload)
    assert r["valid"] == [] and r["rejected"] == []


def test_the_correct_option_is_resolved_by_its_text_before_its_index():
    assert gen.resolve_correct(OPTS, [0], "obj == null") == [3]                      # the index is wrong, the text is right: the text wins
    assert gen.resolve_correct(OPTS, [3], "  OBJ == NULL ") == [3]                   # whitespace / case tolerant
    assert gen.resolve_correct(OPTS, [0, 3], ["Object.isNull(Object obj)", "obj == null"]) == [0, 3]
    assert gen.resolve_correct(OPTS, 2, None) == [2] and gen.resolve_correct(OPTS, [1], "[0]") == [1]    # text that is not an option: fall back to the index


def test_a_one_based_key_is_shifted_only_when_it_cannot_be_valid_zero_based():
    assert gen.resolve_correct(OPTS, [4], None) == [3]                               # 4 of 4 options: out of range as 0-based, valid as 1-based
    assert gen.resolve_correct(OPTS, [2], None) == [2] and gen.resolve_correct(OPTS, [0], None) == [0] and gen.resolve_correct(OPTS, [3], None) == [3]
    assert gen.resolve_correct(OPTS, [9], None) == [9] and gen.resolve_correct(OPTS, [0, 4], None) == [0, 4]     # nonsense stays nonsense and is rejected by validation
    r = run({"questions": [q(correct=[4], answer=None)]})
    assert len(r["valid"]) == 1 and r["valid"][0]["correct"] == [3]
    assert run({"questions": [q(correct=[9], answer=None)]})["rejected"]


def test_a_confused_question_whose_answer_text_and_index_disagree_is_rejected_not_guessed():
    r = run({"questions": [q(correct=[0], answer="obj == null")]})
    assert r["valid"] == [] and any("different options" in e for e in r["rejected"][0][1])
    assert gen.keys_disagree(OPTS, [4], "obj == null") is False                      # 1-based index that points at the same option: consistent
    assert gen.keys_disagree(OPTS, [0], "no such option") is False and gen.keys_disagree(OPTS, [0], None) is False


def test_the_prompt_asks_for_an_object_a_text_answer_and_compact_output():
    seen = []

    async def call(prompt):
        seen.append(prompt); return json.dumps({"questions": [q()]}), 1, 1
    asyncio.run(gen.generate("Java", 3, call=call, difficulties=[1, 2, 3]))
    p = seen[0]
    assert "exactly 3 questions" in p and "ONE JSON object" in p.replace("one JSON object", "ONE JSON object") and '"answer"' in p and "Keep it compact" in p


def test_router_call_streams_and_reports_each_complete_question(monkeypatch):
    """Progress must move while a batch is still being written, not jump 0 -> 100% when the whole response returns."""
    import app.ai.runtime as rt
    from app.ai.types import StreamEnd
    payload = json.dumps({"questions": [q(question_text=f"Question number {i}?") for i in range(3)]})
    pieces = [payload[i:i + 40] for i in range(0, len(payload), 40)]

    class R:
        async def generate_stream(self, prompt, params=None, req=None):
            for p in pieces:
                yield p
            params.on_stream_end(StreamEnd("fake", "m", "stop", 7, 9))

    monkeypatch.setattr(rt, "get_router", lambda: R())
    seen: list[int] = []
    text, tin, tout = asyncio.run(gen.router_call("p", on_item=seen.append))
    assert text == payload and (tin, tout) == (7, 9)
    assert sorted(set(seen)) == [1, 2, 3] and seen == sorted(seen)  # 1, then 2, then 3 questions complete, in order
