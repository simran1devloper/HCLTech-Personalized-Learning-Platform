import pytest

from app.domains.content_engine.versioning import (Versions, decide_regeneration, is_material_change, next_version,
                                                   schema_compatible)
from services import prompt_registry as pr

V = Versions("2.0", "1.0.0", 3, "llama3.1:8b", "ollama")


# ---------------- versioning ----------------
@pytest.mark.parametrize("old,new,material", [("1.0.0", "1.0.1", False), ("1.0.0", "1.1.0", True), ("1.4.2", "2.0.0", True), ("1.0.0", "1.0.0", False)])
def test_material_change_rules(old, new, material):
    assert is_material_change(old, new) is material
    with pytest.raises(ValueError):
        is_material_change("v1", "1.0.0")


def test_schema_compatibility_is_by_major_version():
    assert schema_compatible("2.0", "2.3") and not schema_compatible("1.9", "2.0")


def test_unchanged_content_is_never_regenerated():
    d = decide_regeneration(V, V)
    assert not d.regenerate and d.reasons == ()


def test_each_trigger_regenerates_with_its_own_reason():
    cases = {
        "user_requested": dict(user_requested=True), "quality_rejected": dict(quality_status="rejected"), "outdated": dict(outdated=True),
        "sources_changed": dict(sources_changed=True),
    }
    for reason, kw in cases.items():
        d = decide_regeneration(V, V, **kw)
        assert d.regenerate and d.reasons == (reason,)
    assert decide_regeneration(V, Versions("3.0", "1.0.0")).reasons == ("schema_incompatible",)
    assert decide_regeneration(V, Versions("2.0", "1.1.0")).reasons == ("prompt_changed_materially",)


def test_patch_prompt_and_model_changes_do_not_invalidate_by_default():
    assert not decide_regeneration(V, Versions("2.0", "1.0.5", 3, "other-model", "gemini")).regenerate
    assert decide_regeneration(V, Versions("2.0", "1.0.0", 3, "other-model", "gemini"), model_sensitive=True).reasons == ("model_changed",)


def test_unrelated_section_prompt_change_does_not_invalidate_other_sections():
    stored = {s: Versions("2.0", pr.prompt_version(s)) for s in pr.SECTION_PROMPTS}
    bumped = {**{s: pr.prompt_version(s) for s in pr.SECTION_PROMPTS}, "deep_dive": "1.2.0"}
    decisions = {s: decide_regeneration(stored[s], Versions("2.0", bumped[s])).regenerate for s in stored}
    assert [s for s, regen in decisions.items() if regen] == ["deep_dive"]


def test_next_version_increments_content_version_and_records_current_provenance():
    nv = next_version(V, Versions("2.0", "1.1.0", 1, "m2", "p2"))
    assert (nv.content_version, nv.prompt_version, nv.model) == (4, "1.1.0", "m2")


# ---------------- prompt registry ----------------
def test_every_section_has_a_semver_and_the_guide_replaces_best_practices():
    assert "best_practices" not in pr.SECTION_PROMPTS and "practical_guide" in pr.SECTION_PROMPTS
    for s in pr.SECTION_PROMPTS:
        assert is_material_change("1.0.0", "1.0.0") is False and pr.prompt_version(s).count(".") == 2


def test_prompt_prefix_is_stable_across_requests_and_only_the_tail_varies():
    a = pr.build_section_prompt("deep_dive", "Decorators", "Beginner", 3)
    b = pr.build_section_prompt("deep_dive", "Kubernetes", "Expert", 9)
    prefix = pr.stable_prefix("deep_dive")
    assert a.startswith(prefix) and b.startswith(prefix) and a != b
    assert a[len(prefix):] != b[len(prefix):]
    assert "Decorators" not in prefix and "Beginner" not in prefix          # nothing request-specific leaks into the prefix
    assert pr.stable_prefix("summary") != prefix


def test_fingerprint_changes_only_when_a_version_changes(monkeypatch):
    base = pr.guide_prompt_fingerprint()
    assert base == pr.guide_prompt_fingerprint() and base.startswith("g")
    patched = dict(pr.SECTION_PROMPTS); patched["summary"] = pr.SectionPrompt("summary", "1.1.0", "markdown", 700, "x")
    monkeypatch.setattr(pr, "SECTION_PROMPTS", patched)
    assert pr.guide_prompt_fingerprint() != base


def test_all_prompts_fit_a_4096_token_context_with_their_output_budget_and_sizes_are_recorded():
    sizes = {}
    for s, p in pr.SECTION_PROMPTS.items():
        text = pr.build_section_prompt(s, "Distributed systems consensus algorithms", "Intermediate", 6)
        sizes[s] = pr.prompt_token_estimate(text)
        assert sizes[s] + p.max_tokens <= 4096, (s, sizes[s], p.max_tokens)
    assert max(sizes.values()) < 600                      # with few-shot examples and the Mermaid rules switched on


def test_prompts_without_examples_stay_compact(monkeypatch):
    from app.core.config import get_settings
    for flag in ("CONTENT_FEWSHOT", "CONTENT_MERMAID"):
        monkeypatch.setattr(get_settings(), flag, False, raising=False)
    sizes = {s: pr.prompt_token_estimate(pr.build_section_prompt(s, "Distributed systems", "Intermediate", 6)) for s in pr.SECTION_PROMPTS}
    assert max(sizes.values()) < 400                      # no redundant boilerplate


def test_practical_guide_prompt_lists_every_contract_part_in_order():
    from app.domains.content_engine.contracts import CONTRACTS
    text = pr.SECTION_PROMPTS["practical_guide"].instructions.lower()
    for kw in ["problem", "requirements", "technology selection", "architecture", "component", "project structure", "step-by-step",
               "apis and data models", "testing", "debugging", "performance", "deployment"]:
        assert kw in text, kw
    assert CONTRACTS["practical_guide"].must_include_types == ("code",) and "fenced code" in text
