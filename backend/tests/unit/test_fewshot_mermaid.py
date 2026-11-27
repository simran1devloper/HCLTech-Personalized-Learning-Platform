"""PR3 of content-generation v2: few-shot templates, Mermaid rules/validation/sanitising."""
import json

import pytest

from app.core.config import get_settings
from app.domains.content_engine import study_schemas as ss
from app.domains.content_engine.validators import mermaid as mm
from services import content_generation as cg
from services import prompt_registry as pr

SEC = {s.name: s for s in cg.SECTIONS}


# ------------------------------------------------------------------ few-shot
@pytest.mark.parametrize("section", sorted(ss.SECTION_MODELS))
def test_json_examples_are_valid_against_the_section_schema(section):
    for ex in pr.FEW_SHOT[section]:
        ss.validate_section(section, json.loads(ex.output))   # an example that fails our own validator would teach the wrong thing


def test_viva_example_follows_the_format_the_instructions_ask_for():
    out = pr.FEW_SHOT["viva_voice"][0].output
    assert out.startswith("### Q:") and "**Answer:**" in out and "**Follow-up:**" in out and "🟢" in out


def test_examples_sit_after_the_format_rule_and_before_the_request_specific_tail():
    prompt = pr.build_section_prompt("flashcards", "Python decorators", "Beginner", 3)
    i_fmt, i_ex, i_tail = prompt.index("Output ONLY valid JSON"), prompt.index("EXAMPLE (topic: SQL indexes"), prompt.index("Topic: Python decorators")
    assert i_fmt < i_ex < i_tail
    assert pr.build_section_prompt("summary", "x", "Beginner", 3).count("EXAMPLE") == 0   # only the fragile sections carry examples


def test_example_is_dropped_when_the_learner_picks_the_examples_own_topic():
    assert "EXAMPLE" not in pr.build_section_prompt("flashcards", "SQL Indexes", "Beginner", 3)
    assert "EXAMPLE" not in pr.build_section_prompt("flashcards", "sql indexes explained", "Beginner", 3)
    assert "EXAMPLE" in pr.build_section_prompt("flashcards", "Rust lifetimes", "Beginner", 3)


def test_prefix_stays_identical_across_levels_so_provider_prompt_caching_works():
    a, b = (pr.build_section_prompt("key_concepts", t, lv, sc) for t, lv, sc in (("Rust", "Beginner", 2), ("Go", "Expert", 9)))
    assert a.startswith(pr.stable_prefix("key_concepts")) and b.startswith(pr.stable_prefix("key_concepts"))


@pytest.mark.parametrize("section", sorted(pr.SECTION_PROMPTS))
def test_every_prompt_fits_the_configured_context_window(section):
    st, p = get_settings(), pr.SECTION_PROMPTS[section]
    est = pr.prompt_token_estimate(pr.build_section_prompt(section, "Distributed systems consensus algorithms", "Expert", 9))
    ctx = st.OLLAMA_NUM_CTX or 4096
    assert est + p.max_tokens + 512 <= ctx, (section, est, p.max_tokens, ctx)


def test_flags_switch_examples_and_diagram_rules_off_and_change_the_cache_fingerprint(monkeypatch):
    on = pr.guide_prompt_fingerprint()
    monkeypatch.setattr(get_settings(), "CONTENT_FEWSHOT", False, raising=False)
    assert "EXAMPLE" not in pr.build_section_prompt("key_concepts", "Rust", "Beginner", 3) and pr.guide_prompt_fingerprint() != on
    monkeypatch.setattr(get_settings(), "CONTENT_MERMAID", False, raising=False)
    assert "mermaid" not in pr.build_section_prompt("deep_dive", "Rust", "Beginner", 3)


def test_mermaid_rules_only_in_the_long_sections_and_the_sample_diagram_is_itself_valid():
    for s in pr.SECTION_PROMPTS:
        assert ("```mermaid" in pr.build_section_prompt(s, "Rust", "Beginner", 3)) == (s in pr.MERMAID_SECTIONS), s
    out, errs = mm.process_markdown(pr.MERMAID_RULES[pr.MERMAID_RULES.rindex("```mermaid"):], final=False)
    assert errs == [] and "flowchart TD" in out


# ------------------------------------------------------------------ mermaid validator
GOOD = "```mermaid\nflowchart TD\n    A[\"Client\"] --> B[\"Server\"]\n    B -->|ok| C[\"Done\"]\n```"


def test_valid_diagram_passes_through_unchanged():
    text = f"## Flow\n\n{GOOD}\n\nMore text."
    assert mm.process_markdown(text, final=False) == (text, [])


def test_text_without_diagrams_is_untouched_including_whitespace():
    text = "Intro\n\n\n\nBody with `code`.\n"
    assert mm.process_markdown(text, final=True) == (text, [])


def test_unquoted_parentheses_in_labels_are_fixed_automatically_but_shapes_are_kept():
    src = "flowchart TD\n A[Parse (request)] --> B[(Database)]\n B --> C[/Input/]\n C --> D{Valid?}"
    fixed = mm.autofix(src)
    assert 'A["Parse (request)"]' in fixed and "B[(Database)]" in fixed and "C[/Input/]" in fixed and "D{Valid?}" in fixed
    assert mm.check(fixed) == []


def test_dangerous_constructs_are_stripped():
    src = ('%%{init: {"securityLevel": "loose"}}%%\nflowchart TD\n    A["x <script>alert(1)</script>"] --> B["y <img src=x onerror=1>"]\n'
           '    click A "javascript:alert(1)"\n    A --> C["a<br/>b"]')
    out = mm.sanitize(src)
    for bad in ("%%{", "<script", "alert", "<img", "click", "javascript"):
        assert bad not in out, bad
    assert "<br/>" in out and "flowchart TD" in out


@pytest.mark.parametrize("src,fragment", [
    ("foo bar\n A --> B", "unknown diagram type"),
    ("flowchart TD\n A[\"x\" --> B", "unbalanced"),
    ("flowchart TD\n A[\"one\"]\n B[\"two\"]", "no edges"),
    ("sequenceDiagram\n participant A", "no messages"),
    ("", "empty"),
])
def test_invalid_diagrams_are_reported_in_words_a_model_can_act_on(src, fragment):
    assert any(fragment in e for e in mm.check(src)), mm.check(src)


# ------------------------------------------------------------------ pipeline integration
BAD = "```mermaid\nfoo bar\n```"
BODY = "## Deep dive\n\n" + "Real content about the topic. " * 6 + "\n\n"


def test_final_attempt_drops_a_broken_diagram_instead_of_failing_the_section():
    out = cg.parse_section(SEC["deep_dive"], BODY + BAD + "\n\nEnd of section text.", False)       # final defaults to True
    assert "mermaid" not in out and "Real content" in out and "End of section text." in out


def test_earlier_attempt_raises_a_repairable_violation():
    with pytest.raises(ss.SchemaViolation, match="diagram 1"):
        cg.parse_section(SEC["deep_dive"], BODY + BAD, False, final=False)


async def test_broken_diagram_triggers_one_repair_without_echoing_the_long_answer():
    prompts = []

    async def call(sec, prompt):
        prompts.append(prompt)
        return (BODY + BAD, False) if len(prompts) == 1 else (BODY + GOOD, False)
    value, ok = await cg.generate_section(SEC["deep_dive"], "Rust", "Beginner", 3, call)
    assert ok and "flowchart TD" in value and len(prompts) == 2
    assert "unknown diagram type" in prompts[1] and "Write the complete section again" in prompts[1] and "Real content" not in prompts[1]


async def test_section_survives_when_the_repair_also_fails_the_diagram():
    async def call(sec, prompt):
        return BODY + BAD, False
    value, ok = await cg.generate_section(SEC["deep_dive"], "Rust", "Beginner", 3, call)
    assert ok and "mermaid" not in value and "Real content" in value
