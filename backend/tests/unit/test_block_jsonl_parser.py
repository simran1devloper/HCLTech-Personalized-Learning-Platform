import json
import random

import pytest

from app.domains.content_engine.jsonl import BlockError, BlockStreamParser, ParsedBlock, parse_all, repair_and_load

B1 = {"type": "heading", "level": 2, "text": "Intro"}
B2 = {"type": "text", "markdown": "Some **text** with {braces} and \"quotes\"."}
B3 = {"type": "code", "language": "python", "code": "d = {'a': 1}\nprint(d)\n}"}
B4 = {"type": "list", "ordered": False, "items": ["a", "b"]}


def lines(*blocks):
    return "\n".join(json.dumps(b) for b in blocks) + "\n"


def blocks(items):
    return [i for i in items if isinstance(i, ParsedBlock)]


def errors(items):
    return [i for i in items if isinstance(i, BlockError)]


def summary(items):
    return [("ok", i.ordinal, i.block.id, i.block.type) if isinstance(i, ParsedBlock) else ("err", i.ordinal, i.stage) for i in items]


def test_parses_one_block_per_line_and_assigns_stable_ids():
    items = parse_all("deep_dive", lines(B1, B2, B3, B4))
    assert [b.block.type for b in blocks(items)] == ["heading", "text", "code", "list"] and not errors(items)
    assert [b.ordinal for b in blocks(items)] == [0, 1, 2, 3]
    assert summary(items) == summary(parse_all("deep_dive", lines(B1, B2, B3, B4)))     # deterministic ids
    assert blocks(items)[2].block.code.endswith("}")                                      # braces inside strings survive


def test_chunk_boundary_independence_for_random_splits():
    text = "Here are the blocks:\n```json\n" + lines(B1, B2, B3, B4) + "```\nDone."
    expected = summary(parse_all("s", text))
    rng = random.Random(7)
    for _ in range(200):
        cuts = sorted(rng.sample(range(1, len(text)), rng.randint(1, 25)))
        parts = [text[a:b] for a, b in zip([0] + cuts, cuts + [len(text)])]
        p = BlockStreamParser("s")
        got = []
        for part in parts:
            got += p.feed(part)
        got += p.finish()
        assert summary(got) == expected
    p = BlockStreamParser("s")                                                           # worst case: one char at a time
    got = [x for ch in text for x in p.feed(ch)] + p.finish()
    assert summary(got) == expected


def test_pretty_printed_fenced_prose_and_array_wrapped_output_is_accepted():
    pretty = json.dumps(B2, indent=2)
    text = f"Sure! Here you go:\n```json\n[\n{json.dumps(B1)},\n{pretty},\n{json.dumps(B4)}\n]\n```\nHope that helps {{really}}."
    items = parse_all("s", text)
    assert [b.block.type for b in blocks(items)] == ["heading", "text", "list"] and not errors(items)


def test_deterministic_repairs_trailing_comma_and_raw_newline():
    items = parse_all("s", '{"type": "text", "markdown": "line one\nline two",}\n')
    assert len(blocks(items)) == 1 and blocks(items)[0].repaired and "line two" in blocks(items)[0].block.markdown
    assert repair_and_load('{"a": 1,}') == ({"a": 1}, True)
    with pytest.raises(ValueError):
        repair_and_load('{"a": ')
    p = BlockStreamParser("s"); p.feed(lines(B1)); assert p.stats["repaired"] == 0


def test_one_bad_block_never_discards_its_neighbours_and_keeps_ordinals():
    bad_schema = {"type": "heading", "level": 9, "text": "x"}
    bad_syntax = '{"type": "text", "markdown": }'
    unsafe = {"type": "text", "markdown": "<script>alert(1)</script>"}
    text = lines(B1) + json.dumps(bad_schema) + "\n" + bad_syntax + "\n" + json.dumps(unsafe) + "\n" + lines(B4)
    items = parse_all("s", text)
    assert [b.ordinal for b in blocks(items)] == [0, 4]                                  # failed objects consumed ordinals 1-3
    assert [(e.ordinal, e.stage) for e in errors(items)] == [(1, "schema"), (2, "syntax"), (3, "schema")]
    assert "level" in errors(items)[0].message


def test_ids_stay_stable_when_only_one_block_is_regenerated():
    first = parse_all("s", lines(B1, {"type": "heading", "level": 9, "text": "x"}, B4))
    fixed = parse_all("s", lines(B1, {"type": "heading", "level": 3, "text": "x"}, B4))
    id_of = lambda items, o: next(b.block.id for b in blocks(items) if b.ordinal == o)
    assert id_of(first, 0) == id_of(fixed, 0) and id_of(first, 2) == id_of(fixed, 2)     # neighbours keep their identity


def test_truncated_output_is_reported_not_silently_dropped():
    items = parse_all("s", lines(B1) + '{"type": "text", "markdown": "cut off here')
    assert len(blocks(items)) == 1 and [(e.stage, e.ordinal) for e in errors(items)] == [("truncated", 1)]


def test_prose_braces_are_not_mistaken_for_blocks():
    items = parse_all("s", 'Use {curly} braces or {1, 2} sets. ' + lines(B1))
    assert len(blocks(items)) == 1 and not errors(items)


def test_limits_oversized_blocks_and_block_count():
    big = {"type": "text", "markdown": "x" * 20000}
    p = BlockStreamParser("s", max_object_chars=500)
    items = p.feed(lines(B1, big, B4)) + p.finish()
    assert [b.ordinal for b in blocks(items)] == [0, 2] and [e.stage for e in errors(items)] == ["limit"]
    many = lines(*[{"type": "heading", "level": 2, "text": f"h{i}"} for i in range(10)])
    q = BlockStreamParser("s", max_blocks=3)
    items = q.feed(many) + q.finish()
    assert len(blocks(items)) == 3 and [e.stage for e in errors(items)] == ["limit"]


def test_non_object_json_and_empty_input():
    assert parse_all("s", "") == [] and parse_all("s", "just prose, no blocks") == []
    assert [e.stage for e in errors(parse_all("s", '{"a": 1}'))] == ["schema"]            # object without a valid type
    assert summary(parse_all("s", lines({"type": "HEADING", "level": 2, "text": "Case"}))) [0][3] == "heading"   # type is normalised
