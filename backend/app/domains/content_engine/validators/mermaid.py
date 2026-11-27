"""Mermaid diagrams inside generated Markdown: sanitise -> auto-fix -> validate. Deterministic, no browser needed.

Models write ```mermaid fences that are *almost* right (unquoted labels with parentheses, stray HTML, init directives).
We (1) strip anything that could execute or restyle the page, (2) fix the one mistake that is mechanical to fix
(parentheses in unquoted node labels), then (3) check what is left with the same structural rules the block validator uses.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from app.domains.content_engine.validators.basic import validate_mermaid_structure

ALLOWED_TYPES = ("flowchart", "graph", "sequenceDiagram", "stateDiagram", "stateDiagram-v2", "classDiagram",
                 "erDiagram", "gantt", "pie", "mindmap")
MAX_LINES = 80

FENCE = re.compile(r"(?P<open>^[ \t]*```[ \t]*mermaid[^\n]*\n)(?P<src>.*?)(?P<close>^[ \t]*```[ \t]*$)", re.S | re.I | re.M)
_DIRECTIVE = re.compile(r"%%\{.*?\}%%", re.S)
_SCRIPT = re.compile(r"<\s*(script|iframe|object|embed|style)\b.*?(<\s*/\s*\1\s*>|$)", re.S | re.I)
_TAG = re.compile(r"<(?!\s*br\s*/?\s*>)[^>\n]*>", re.I)       # any HTML tag except <br>
_SQUARE_LABEL = re.compile(r'(?P<id>\b[A-Za-z0-9_-]+)\[(?P<label>[^\]\n"]+)\]')


def sanitize(src: str) -> str:
    """Remove init directives, click/href handlers, javascript: URLs and HTML (except <br>)."""
    src = _DIRECTIVE.sub("", src)
    src = _SCRIPT.sub("", src)
    src = _TAG.sub("", src)
    src = re.sub(r"javascript\s*:", "", src, flags=re.I)
    return "\n".join(l for l in src.splitlines() if not re.match(r"\s*(click|href|callback|link)\b", l, re.I)).strip("\n")


def _quote_label(m: "re.Match[str]") -> str:
    label = m.group("label")
    if label[0] in "([{/\\":                            # real shape syntax: [(db)], [/io/], [[sub]]
        return m.group(0)
    if not re.search(r"[(){}]", label):
        return m.group(0)
    return f'{m.group("id")}["{label.strip()}"]'


def autofix(src: str) -> str:
    """Quote flowchart labels that contain parentheses/braces (the most common cause of 'Syntax error in text')."""
    kind = _kind(src)
    if kind not in ("flowchart", "graph"):
        return src
    return "\n".join(_SQUARE_LABEL.sub(_quote_label, line) for line in src.splitlines())


def _kind(src: str) -> str:
    for line in src.splitlines():
        t = line.strip()
        if t and not t.startswith("%%"):
            return t.split()[0]
    return ""


def check(src: str) -> List[str]:
    """Problems with an (already sanitised) diagram, phrased so they can be sent back to the model."""
    if not src.strip():
        return ["the diagram is empty"]
    errors: List[str] = []
    kind = _kind(src)
    if kind not in ALLOWED_TYPES:
        errors.append(f"unknown diagram type '{kind}': start with 'flowchart TD' or 'sequenceDiagram'")
    if len(src.splitlines()) > MAX_LINES:
        errors.append(f"diagram has more than {MAX_LINES} lines: keep it to about 12 nodes")
    if kind in ("flowchart", "graph"):
        bare = re.sub(r'"[^"\n]*"', '""', src)           # ignore quoted label text
        for o, c, name in (("[", "]", "square brackets"), ("(", ")", "parentheses"), ("{", "}", "braces")):
            if bare.count(o) != bare.count(c):
                errors.append(f"unbalanced {name}: put node labels in double quotes, e.g. A[\"Parse request\"]")
    errors += [f.message for f in validate_mermaid_structure([{"type": "mermaid", "id": "diagram", "source": src}])]
    return errors


def process_markdown(text: str, *, final: bool = True) -> Tuple[str, List[str]]:
    """Clean every ```mermaid block in `text`. Returns (text, errors).

    errors is non-empty only when `final` is False and a diagram is still invalid after fixing (the caller then asks the
    model to try again). When `final` is True an unfixable diagram is removed so the rest of the section survives."""
    errors: List[str] = []
    n = 0
    if not FENCE.search(text):
        return text, errors

    def repl(m: "re.Match[str]") -> str:
        nonlocal n
        n += 1
        fixed = autofix(sanitize(m.group("src")))
        problems = check(fixed)
        if not problems:
            return f'{m.group("open")}{fixed}\n{m.group("close")}'
        if final:
            return ""
        errors.extend(f"diagram {n}: {p}" for p in problems)
        return m.group(0)

    out = FENCE.sub(repl, text)
    if final:
        out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out, errors
