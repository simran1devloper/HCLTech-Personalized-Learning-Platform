import pytest

from app.domains.content_engine.contracts import CONTRACTS, LEGACY_ALIASES, SECTION_IDS, validate_section


def H(text, level=2): return {"type": "heading", "level": level, "text": text, "id": f"h-{text[:8]}-{level}"}
def T(md="Some explanatory text that is long enough."): return {"type": "text", "markdown": md, "id": "t"}
def C(): return {"type": "code", "language": "python", "code": "print(1)", "id": "c"}


def satisfying(section):
    """Minimal block list that satisfies a contract, built from the contract itself."""
    c = CONTRACTS[section]
    blocks = []
    if section == "flashcards":
        return [{"type": "flashcard", "front": f"q{i}", "back": f"a{i}", "id": f"fc-{i}"} for i in range(5)]
    if c.scope_level is not None:
        for n in range(c.min_scopes):
            blocks.append(H(f"Concept {n}", c.scope_level))
            for el in c.scope_elements:
                blocks += [H(el.keywords[0].title(), c.scope_level + 1), T()]
    for el in c.elements:
        if not el.optional:
            blocks += [H(el.keywords[0].title()), *( [{"type": t, "id": "x"} if t != "list" else {"type": "list", "items": ["a"], "id": "l"} for t in el.block_types[:1]] or [T()])]
    for t in c.must_include_types:
        blocks.append(C())
    while len(blocks) < c.min_blocks:
        blocks.append(T())
    return blocks


def test_nine_contracts_in_the_agreed_order_with_the_new_guide_section():
    assert SECTION_IDS == ("summary", "key_concepts", "deep_dive", "real_world_applications", "interview_qa", "practical_guide",
                           "learning_roadmap", "flashcards", "recommendations")
    assert "best_practices" not in CONTRACTS and LEGACY_ALIASES["best_practices"] == "practical_guide" and LEGACY_ALIASES["viva_voice"] == "interview_qa"


@pytest.mark.parametrize("section", SECTION_IDS)
def test_a_minimal_conforming_section_is_complete(section):
    rep = validate_section(section, satisfying(section))
    assert rep.complete, (rep.missing, [f.message for f in rep.findings])
    assert rep.coverage == 1.0


@pytest.mark.parametrize("section", [s for s in SECTION_IDS if s != "flashcards"])
def test_an_empty_section_fails_with_specific_messages(section):
    rep = validate_section(section, [])
    assert not rep.complete and any(f.code == "contract_too_few_blocks" for f in rep.findings)
    assert rep.coverage < 1.0 or CONTRACTS[section].scope_level is not None


def test_practical_guide_requires_every_part_and_a_code_block():
    blocks = satisfying("practical_guide")
    assert validate_section("best_practices", blocks).complete                       # legacy id resolves to the new contract
    no_testing = [b for b in blocks if "testing" not in str(b.get("text", "")).lower()]
    rep = validate_section("practical_guide", no_testing)
    assert rep.missing == ["testing"] and rep.coverage == pytest.approx(11 / 12)
    no_code = [b for b in blocks if b["type"] != "code"]
    assert any(f.code == "contract_missing_block_type" for f in validate_section("practical_guide", no_code).findings)


def test_optional_elements_are_never_forced():
    blocks = satisfying("deep_dive")
    assert not any("math" in str(b.get("text", "")).lower() for b in blocks)
    assert validate_section("deep_dive", blocks).complete                           # no mathematical foundation required


def test_disallowed_block_types_and_size_limits():
    blocks = satisfying("recommendations") + [{"type": "chart", "id": "ch", "chart_type": "bar"}]
    assert any(f.code == "contract_block_not_allowed" for f in validate_section("recommendations", blocks).findings)
    many = satisfying("flashcards") + [{"type": "flashcard", "front": "q", "back": "a", "id": f"x{i}"} for i in range(40)]
    assert any(f.code == "contract_too_many_blocks" for f in validate_section("flashcards", many).findings)
    few = satisfying("flashcards")[:3]
    codes = {f.code for f in validate_section("flashcards", few).findings}
    assert {"contract_too_few_blocks", "contract_too_few_of_type"} <= codes


def test_elements_can_be_announced_by_bold_labels_titles_or_headings():
    base = [H("Intro"), T("x" * 30)] * 2
    via_bold = base + [T("**Definition:** it is X"), T("**Why it matters:** because"), {"type": "list", "items": ["**Learning objectives:** a"], "id": "l"},
                       T("**Prerequisites:** none"), T("**How it works:** like so"), T("**Topic map:** a, b")]
    assert validate_section("summary", via_bold).complete
    titled = base + [{"type": "text", "markdown": "y" * 30, "title": kw, "id": f"t{i}"} for i, kw in
                     enumerate(["Definition", "Importance", "Learning objectives", "Prerequisites", "Workflow", "Topic map"])]
    assert validate_section("summary", titled).complete


def test_key_concepts_are_checked_per_concept():
    blocks = satisfying("key_concepts")
    assert validate_section("key_concepts", blocks).complete
    first_intuition = next(i for i, b in enumerate(blocks) if b.get("text") == "Intuition")
    removed = blocks[:first_intuition] + blocks[first_intuition + 2:]
    rep = validate_section("key_concepts", removed)
    assert any(m.startswith("intuition@Concept 0") for m in rep.missing) and not rep.complete
    assert any(f.code == "contract_too_few_scopes" for f in validate_section("key_concepts", blocks[:12]).findings)


def test_accepts_validated_model_instances_as_well_as_dicts():
    from app.domains.content_engine.schema import validate_block
    inst = [validate_block(b) for b in satisfying("flashcards")]
    assert validate_section("flashcards", inst).complete
