import random
import shutil
import sys

import pytest

from app.domains.content_engine.validators import KatexRunner, validate_blocks
from app.domains.content_engine.validators.basic import (validate_code, validate_duplicates, validate_ids,
                                                         validate_mermaid_structure, validate_references, validate_structure)
from app.domains.content_engine.validators.latex import validate_latex

node = pytest.mark.skipif(not KatexRunner().available(), reason="Node + KaTeX not available")


def codes(fs, *sev):
    return [f.code for f in fs if not sev or f.severity in sev]


def B(type_, id_="blk-001", **kw): return {"type": type_, "id": id_, **kw}


# ---------------- ids ----------------
def test_ids_must_be_present_well_formed_and_unique():
    assert validate_ids([B("text", "blk-a", markdown="x"), B("text", "blk-b", markdown="y")]) == []
    bad = validate_ids([B("text", "ok-1"), B("text", "ok-1"), {"type": "text"}, B("text", "x y")])
    assert codes(bad) == ["block_id_duplicate", "block_id_duplicate", "block_id_invalid", "block_id_invalid"]


# ---------------- duplicates ----------------
LONG = "the quick brown fox jumps over the lazy dog near the old stone bridge every single morning"


def test_exact_and_near_duplicates_are_found_short_text_is_ignored():
    blocks = [B("text", "blk-1", markdown=LONG), B("text", "blk-2", markdown=LONG.upper() + "!"),
              B("text", "blk-3", markdown=LONG + " today"), B("text", "blk-4", markdown="short text"), B("text", "blk-5", markdown="short text")]
    fs = validate_duplicates(blocks)
    assert [(f.code, f.block_id) for f in fs] == [("duplicate_block", "blk-2")] + [("near_duplicate_block", "blk-3")]
    assert validate_duplicates([B("text", "blk-1", markdown=LONG), B("text", "blk-2", markdown="a completely different paragraph about databases and indexes in modern systems")]) == []
    assert validate_duplicates([B("text", "blk-1", markdown=LONG), B("callout", "blk-2", text=LONG)]) == []      # different types never collide


# ---------------- mermaid ----------------
@pytest.mark.parametrize("src", [
    "flowchart TD\n A[Start] --> B[End]", "graph LR\n A-->B\n B-->C", "sequenceDiagram\n A->>B: hi\n B-->>A: ok",
    "sequenceDiagram\n A->>B: x\n alt ok\n B->>A: y\n end", "stateDiagram-v2\n [*] --> Idle\n Idle --> Busy", "classDiagram\n class A\n A <|-- B",
    "erDiagram\n USER ||--o{ ORDER : places", 'pie\n "a" : 3\n "b" : 7', "mindmap\n  root\n    child",
    "gantt\n dateFormat YYYY-MM-DD\n section A\n t :2024-01-01, 3d", "journey\n title t\n section S\n step: 5: me",
])
def test_valid_mermaid_diagrams_pass(src):
    assert validate_mermaid_structure([B("mermaid", source=src)]) == []


@pytest.mark.parametrize("src,code", [
    ("flowchart TD\n A", "mermaid_no_edges"), ("flowchart XX\n A-->B", "mermaid_bad_direction"),
    ("flowchart TD\n subgraph s\n A-->B", "mermaid_unbalanced_subgraph"), ("sequenceDiagram\n participant A", "mermaid_no_messages"),
    ("sequenceDiagram\n A->>B: x\n alt c\n B->>A: y", "mermaid_unbalanced_blocks"), ("stateDiagram-v2\n [*]", "mermaid_no_transitions"),
    ("erDiagram\n USER", "mermaid_no_relationships"), ("pie\n title nothing", "mermaid_empty_pie"), ("gantt\n title t", "mermaid_gantt_incomplete"),
    ("flowchart TD\n A[\"unterminated --> B", "mermaid_unbalanced_quotes"),
])
def test_structurally_broken_mermaid_is_reported(src, code):
    assert code in codes(validate_mermaid_structure([B("mermaid", source=src)]))


def test_oversized_flowchart_is_rejected():
    src = "flowchart TD\n" + "\n".join(f" N{i} --> N{i+1}" for i in range(200))
    assert "mermaid_too_large" in codes(validate_mermaid_structure([B("mermaid", source=src)]))


# ---------------- code ----------------
def test_code_syntax_checks_by_language():
    ok = [B("code", "blk-1", language="python", code="def f(x):\n    return x + 1\n"), B("code", "blk-2", language="json", code='{"a": [1, 2]}'),
          B("code", "blk-3", language="javascript", code="const f = (a) => { return `x${a}`; } // }"), B("code", "blk-4", language="rust", code="fn main() { let s = \"}\"; }")]
    assert validate_code(ok) == []
    bad = validate_code([B("code", "blk-1", language="python", code="def f(:\n  pass"), B("code", "blk-2", language="json", code="{'a': 1}"),
                         B("code", "blk-3", language="javascript", code="function f() { if (x) { return 1; }"), B("code", "blk-4", language="python", code="```python\nx=1\n```")])
    assert [(f.block_id, f.code, f.severity) for f in bad] == [("blk-1", "code_syntax_error", "error"), ("blk-2", "code_syntax_error", "error"),
                                                              ("blk-3", "code_unbalanced", "warning"), ("blk-4", "code_contains_fence", "error"), ("blk-4", "code_syntax_error", "error")]
    assert validate_code([B("code", "blk-1", language="haskell", code="main = do {")]) == []         # unknown language: not guessed


# ---------------- references ----------------
def cit(label, url="https://docs.python.org/3/", id_="cit-1"): return B("citation", id_, label=label, source_title="Docs", url=url)


def test_references_resolve_and_urls_are_safe():
    good = [B("text", "blk-1", markdown="Decorators wrap functions [1]. See [link](https://x.example) too."), cit("[1]")]
    assert validate_references(good) == []
    fs = validate_references([B("text", "blk-1", markdown="Claim [2]."), cit("[1]", id_="cit-1"), cit("[1]", id_="cit-2")])
    assert sorted(codes(fs)) == ["citation_label_duplicate", "citation_label_duplicate", "citation_missing", "citation_unreferenced", "citation_unreferenced"]
    bad = validate_references([B("text", "blk-1", markdown="x [1]"), cit("[1]", "http://169.254.169.254/latest")])
    assert ("citation_url_unsafe", "critical") in [(f.code, f.severity) for f in bad]


# ---------------- structure / accessibility ----------------
def test_structure_findings():
    fs = validate_structure([B("heading", "h-001", level=2, text="A"), B("heading", "h-002", level=4, text="B"),
                             B("table", "t-001", headers=[" "], rows=[]), B("chart", "c-001", categories=["a", "b"], series=[])])
    assert set(codes(fs)) == {"heading_level_jump", "table_no_headers", "chart_no_text_alternative"}
    assert "section_too_long" in codes(validate_structure([B("text", "t-001", markdown="x" * 61_000)]))


# ---------------- LaTeX (Layer 1 always; Layer 2 when Node is present) ----------------
M = lambda tex, i="m-001": B("math", i, latex=tex, display=True)


def test_latex_policy_layer_runs_without_node_and_flags_partial():
    rep = validate_latex([M(r"\href{javascript:alert(1)}{x}"), M(r"\frac{a}{b"), M(r"\frac{a}{b}")], runner=None)
    assert rep.partial == ["latex_katex_parse"]
    assert ("latex_forbidden_command", "critical") in [(f.code, f.severity) for f in rep.findings]
    assert "latex_unbalanced_braces" in codes(rep.findings)
    clean = validate_latex([M(r"\frac{a}{b}")], runner=None)
    assert clean.ok and clean.partial == ["latex_katex_parse"]                 # valid, but never reported as *fully* validated


@node
def test_katex_layer_is_authoritative_for_syntax_and_fully_validates():
    r = KatexRunner()
    try:
        valid = [r"\sum_{i=1}^{n} i = \frac{n(n+1)}{2}", r"\begin{pmatrix}a&b\\c&d\end{pmatrix}", r"\int_0^\infty e^{-x^2}\,dx = \frac{\sqrt{\pi}}{2}", r"\mathbb{R}^n \to \mathbb{R}"]
        for tex in valid:
            rep = validate_latex([M(tex)], r)
            assert rep.ok and rep.partial == [], (tex, [f.message for f in rep.findings])
        for tex, expect in [("x^^2", "latex_parse_error"), (r"\frac{a}{b", "latex_unbalanced_braces"), (r"\begin{matrix}a\end{pmatrix}", "latex_environment_mismatch")]:
            rep = validate_latex([M(tex)], r)
            assert not rep.ok and expect in codes(rep.findings), tex
        assert "latex_parse_error" in codes(validate_latex([B("math", "m-009", latex=r"\undefinedmacro{x}")], r).findings) or \
               "latex_unknown_command" in codes(validate_latex([B("math", "m-009", latex=r"\undefinedmacro{x}")], r).findings)
    finally:
        r.close()


@node
def test_policy_blocks_inputs_that_katex_itself_would_accept():
    r = KatexRunner()
    try:
        assert r.check(r"\href{javascript:alert(1)}{x}")[0] is True        # KaTeX alone does NOT reject it (trust:false only disables the link)
        rep = validate_latex([M(r"\href{javascript:alert(1)}{x}"), M(r"\def\a{b}\a")], r)
        assert [f.severity for f in rep.findings if f.code == "latex_forbidden_command"] == ["critical", "critical"] and not rep.ok
    finally:
        r.close()


@node
def test_inline_math_in_markdown_is_validated_and_currency_is_not_math():
    r = KatexRunner()
    try:
        rep = validate_latex([B("text", "t-001", markdown=r"Euler: $e^{i\pi}+1=0$ and display $$\frac{a}{b$$ but it costs $5 and $6.")], r)
        assert codes(rep.findings, "error") != [] and "latex_unbalanced_braces" in codes(rep.findings)
        assert validate_latex([B("text", "t-002", markdown="It costs $5 and $6 in total.")], r).findings == []
    finally:
        r.close()


@node
def test_worker_recovers_from_a_timeout_and_a_crash():
    hung = KatexRunner(timeout_s=0.4, worker_cmd=[sys.executable, "-c", "import time,sys;print('{\"ready\":true}',flush=True);time.sleep(60)"])
    ok, msg = hung.check("x")
    assert ok is False and "timed out" in msg
    hung.close()
    flaky = KatexRunner(timeout_s=1.0)
    try:
        assert flaky.check("x^2")[0] is True
        flaky._proc.kill(); flaky._proc.wait()                                  # simulate a crash
        assert flaky.check("x^2")[0] is True and flaky.restarts >= 1             # restarted transparently
    finally:
        flaky.close()


@node
def test_katex_layer_survives_fuzzed_input_without_hanging():
    r, rng = KatexRunner(timeout_s=3), random.Random(11)
    alphabet = list(r"\{}^_$&%#~ abcxyz0123 \frac\sqrt\left\right\begin\end()[]|!") + ["\n"]
    try:
        for _ in range(60):
            tex = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 60)))
            ok, msg = r.check(tex)
            assert isinstance(ok, bool) and isinstance(msg, str)
    finally:
        r.close()


# ---------------- entry point ----------------
def test_validate_blocks_combines_everything_and_reports_degraded_mode():
    blocks = [B("heading", "h-001", level=2, text="Definition"), B("math", "m-001", latex=r"\frac{a}{b}"),
              B("code", "c-001", language="python", code="def f(:\n pass"), B("text", "t-001", markdown="Claim [3].")]
    rep = validate_blocks("summary", blocks, katex=None)
    cs = set(codes(rep.findings))
    assert {"code_syntax_error", "citation_missing", "contract_too_few_blocks", "contract_missing_element"} <= cs
    assert not rep.ok and rep.partial == ["latex_katex_parse"] and rep.as_dict()["ok"] is False
    assert validate_blocks("summary", blocks, katex=None, check_contract=False).worst == "error"
