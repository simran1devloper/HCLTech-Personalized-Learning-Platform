"""Gate G1 evidence: a simulated model emits JSON Lines for every section; parser -> validators -> contracts compose."""
import json
import random

import pytest

from app.domains.content_engine.contracts import CONTRACTS, SECTION_IDS, validate_section
from app.domains.content_engine.jsonl import BlockError, BlockStreamParser, ParsedBlock
from app.domains.content_engine.validators import KatexRunner, validate_blocks

KATEX = KatexRunner()


def words(tag: str, n: int = 14) -> str:
    return " ".join(f"{tag}{i}" for i in range(n))      # unique words per block: no accidental duplicates


def blocks_for(section: str) -> list[dict]:
    """The blocks a competent model would emit for this contract (valid per schema and contract)."""
    c = CONTRACTS[section]
    out: list[dict] = []
    n = 0

    def add(**b):
        nonlocal n
        n += 1
        out.append(b)

    if section == "flashcards":
        for i in range(6):
            add(type="flashcard", front=f"Question {i}: {words('q' + str(i), 5)}?", back=f"Answer {i}: {words('a' + str(i), 6)}.")
        return out
    if c.scope_level is not None:
        for k in range(c.min_scopes):
            add(type="heading", level=c.scope_level, text=f"Concept {k}")
            for el in c.scope_elements:
                add(type="heading", level=c.scope_level + 1, text=el.keywords[0].title())
                add(type="text", markdown=f"{el.id} of concept {k}: {words(f'{el.id}{k}x')}.")
    for el in c.elements:
        if el.optional:
            continue
        add(type="heading", level=2, text=el.keywords[0].title())
        if el.block_types and el.block_types[0] == "list":
            add(type="list", ordered=False, items=[f"{el.id} item one {words(el.id + 'i', 4)}", f"{el.id} item two {words(el.id + 'j', 4)}"])
        else:
            add(type="text", markdown=f"{el.description}: {words(el.id + 't')}.")
    if "code" in c.must_include_types:
        add(type="code", language="python", code="def handler(event):\n    return {'ok': True}\n")
    if section == "deep_dive":
        add(type="math", latex=r"T(n) = \sum_{i=1}^{n} \frac{1}{i}", display=True)
        add(type="mermaid", source="flowchart TD\n A[Request] --> B[Parse]\n B --> C[Respond]")
    while len(out) < c.min_blocks:
        add(type="text", markdown=f"Additional detail {len(out)}: {words(f'pad{len(out)}x')}.")
    return out


def as_jsonl(blocks: list[dict], rng: random.Random) -> str:
    lines = []
    for b in blocks:
        lines.append(json.dumps(b, indent=2) if rng.random() < 0.3 else json.dumps(b))    # models sometimes pretty-print
    return "Here is the section:\n```json\n" + "\n".join(lines) + "\n```\n"


def stream_parse(section: str, text: str, rng: random.Random):
    p, items, i = BlockStreamParser(section), [], 0
    while i < len(text):
        step = rng.randint(1, 40)
        items += p.feed(text[i:i + step]); i += step
    return items + p.finish()


@pytest.mark.parametrize("section", SECTION_IDS)
def test_each_section_streams_parses_and_validates_end_to_end(section):
    rng = random.Random(section)
    items = stream_parse(section, as_jsonl(blocks_for(section), rng), rng)
    errors = [i for i in items if isinstance(i, BlockError)]
    parsed = [i.block for i in items if isinstance(i, ParsedBlock)]
    assert not errors, [(e.stage, e.message) for e in errors]
    rep = validate_blocks(section, parsed, katex=KATEX if KATEX.available() else None)
    assert rep.ok, [(f.code, f.message) for f in rep.findings if f.severity in ("critical", "error")]
    assert not [f for f in rep.findings if f.code == "near_duplicate_block"]
    assert validate_section(section, parsed).coverage == 1.0
    ids = [b.id for b in parsed]
    assert len(ids) == len(set(ids)) and all(i.startswith("b_") for i in ids)
    if KATEX.available() and section == "deep_dive":
        assert rep.partial == []                      # authoritative KaTeX parse ran
    KATEX.close()


def test_an_injected_fault_is_reported_precisely_and_does_not_cost_the_other_blocks():
    section, rng = "deep_dive", random.Random(3)
    good = blocks_for(section)
    broken = good[:3] + [{"type": "math", "latex": r"\frac{a}{b"}, {"type": "code", "language": "python", "code": "def f(:\n  pass"}] + good[3:]
    items = stream_parse(section, as_jsonl(broken, rng), rng)
    errors = [i for i in items if isinstance(i, BlockError)]
    parsed = [i.block for i in items if isinstance(i, ParsedBlock)]
    assert [(e.stage, e.ordinal) for e in errors] == [("schema", 3)]           # the malformed math never became a block
    assert len(parsed) == len(good) + 1                                        # everything else survived (incl. the bad-syntax code block)
    rep = validate_blocks(section, parsed, katex=None)
    assert "code_syntax_error" in {f.code for f in rep.findings} and not rep.ok
    assert validate_blocks(section, [b for b in parsed if b.type != "code"], katex=None).ok is True


def test_removing_a_required_part_is_caught_by_the_contract_not_by_the_model():
    parsed = [i.block for i in stream_parse("practical_guide", as_jsonl(blocks_for("practical_guide"), random.Random(1)), random.Random(1)) if isinstance(i, ParsedBlock)]
    trimmed = [b for b in parsed if not (b.type == "heading" and b.text == "Deployment")]
    rep = validate_section("practical_guide", trimmed)
    assert rep.missing == ["deployment"] and 0 < rep.coverage < 1.0
