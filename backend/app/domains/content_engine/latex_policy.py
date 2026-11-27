"""LaTeX validation, Layer 1: deterministic policy lint (pure Python, always on). See ADR 0007.

Layer 2 (authoritative KaTeX parse in a Node process) lives in ``validators/latex.py``.
Policy violations are *critical*: KaTeX accepts some of them (e.g. ``\\href{javascript:...}`` and ``\\def``)
without throwing, so "it parses" is not "it is allowed".
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from app.domains.content_engine.findings import Finding

MAX_NESTING = 20
_ALLOWLIST_PATH = Path(__file__).parent / "data" / "latex_allowlist.json"

FORBIDDEN = frozenset({
    "href", "url", "includegraphics", "input", "include", "write", "immediate", "openin", "openout", "read",
    "def", "gdef", "edef", "xdef", "let", "futurelet", "newcommand", "renewcommand", "providecommand", "newenvironment",
    "csname", "endcsname", "catcode", "expandafter", "noexpand", "message", "errmessage", "verb", "show",
    "htmlClass", "htmlId", "htmlStyle", "htmlData",
})
SUPPORTED_ENVIRONMENTS = frozenset({
    "matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "smallmatrix", "array", "subarray", "cases", "dcases",
    "rcases", "aligned", "alignedat", "align", "align*", "gather", "gathered", "equation", "equation*", "split", "CD",
})

_CMD = re.compile(r"\\([A-Za-z@]+|.)", re.S)
_BEGIN_END = re.compile(r"\\(begin|end)\s*\{([^{}]*)\}")


@lru_cache(maxsize=1)
def load_allowlist() -> frozenset[str] | None:
    """Commands KaTeX recognises, generated from the installed KaTeX by scripts/generate_katex_allowlist.mjs."""
    try:
        data = json.loads(_ALLOWLIST_PATH.read_text())
    except (OSError, ValueError):
        return None
    return frozenset(data["commands"])


def _strip_escaped(tex: str) -> str:
    return re.sub(r"\\[{}]", "", tex)


def lint_latex(tex: str, block_id: str | None = None) -> list[Finding]:
    out: list[Finding] = []

    def add(code: str, sev: str, msg: str, loc: str | None = None):
        out.append(Finding(code, sev, msg, block_id, loc))  # type: ignore[arg-type]

    if not tex.strip():
        add("latex_empty", "error", "empty expression")
        return out
    if len(tex) > 1000:
        add("latex_too_long", "error", "expression longer than 1000 characters")

    # --- forbidden commands (critical)
    allow = load_allowlist()
    for m in _CMD.finditer(tex):
        name = m.group(1)
        if name in FORBIDDEN:
            add("latex_forbidden_command", "critical", f"command \\{name} is not allowed", f"position {m.start()}")
        elif allow is not None and name.isalpha() and name not in allow and name not in ("begin", "end"):
            add("latex_unknown_command", "error", f"unknown command \\{name}", f"position {m.start()}")

    # --- braces
    depth = peak = 0
    for i, ch in enumerate(_strip_escaped(tex)):
        if ch == "{":
            depth += 1; peak = max(peak, depth)
        elif ch == "}":
            depth -= 1
            if depth < 0:
                add("latex_unbalanced_braces", "error", "closing brace without opening brace", f"position {i}")
                depth = 0
    if depth > 0:
        add("latex_unbalanced_braces", "error", f"{depth} unclosed brace(s)")
    if peak > MAX_NESTING:
        add("latex_too_deep", "error", f"nesting deeper than {MAX_NESTING}")

    # --- \left / \right pairing
    lefts, rights = len(re.findall(r"\\left\b", tex)), len(re.findall(r"\\right\b", tex))
    if lefts != rights:
        add("latex_left_right_mismatch", "error", f"{lefts} \\left vs {rights} \\right")

    # --- environments
    stack: list[str] = []
    for m in _BEGIN_END.finditer(tex):
        kind, env = m.group(1), m.group(2).strip()
        if kind == "begin":
            if env not in SUPPORTED_ENVIRONMENTS:
                add("latex_unknown_environment", "error", f"environment '{env}' is not supported", f"position {m.start()}")
            stack.append(env)
        elif not stack or stack[-1] != env:
            add("latex_environment_mismatch", "error", f"\\end{{{env}}} does not match the open environment", f"position {m.start()}")
            if env in stack:
                while stack and stack.pop() != env:
                    pass
        else:
            stack.pop()
    for env in stack:
        add("latex_environment_unclosed", "error", f"\\begin{{{env}}} is never closed")

    # --- stray dollar signs (math delimiters are added by the renderer)
    if re.search(r"(?<!\\)\$", tex):
        add("latex_stray_dollar", "warning", "unescaped $ inside a math block")
    return out
