"""IDs, duplicates, Mermaid structure, code syntax, references, structure/accessibility. All deterministic."""
from __future__ import annotations

import ast
import json
import re
from collections import Counter
from typing import Sequence

from app.domains.content_engine.findings import Finding
from app.domains.resource_engine.url_safety import validate_url
from app.core.exceptions import UnsafeURLError

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")


# ------------------------------------------------------------------ ids
def validate_ids(blocks: Sequence[dict]) -> list[Finding]:
    out, seen = [], Counter(b.get("id") for b in blocks)
    for b in blocks:
        i = b.get("id")
        if not i or not _ID.match(str(i)):
            out.append(Finding("block_id_invalid", "error", "block id is missing or malformed", i))
        elif seen[i] > 1:
            out.append(Finding("block_id_duplicate", "error", f"block id '{i}' is used {seen[i]} times", i))
    return out


# ------------------------------------------------------------------ duplicates
def _text_of(b: dict) -> str:
    t = b.get("type")
    if t == "text":
        return b.get("markdown", "")
    if t in ("callout", "heading"):
        return b.get("text", "")
    if t == "list":
        return " ".join(b.get("items", []))
    if t == "flashcard":
        return f"{b.get('front', '')} {b.get('back', '')}"
    if t == "exercise":
        return b.get("prompt", "")
    if t == "code":
        return b.get("code", "")
    if t == "algorithm":
        return " ".join(b.get("steps", []))
    return ""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.lower())).strip()


def _shingles(words: list[str], k: int = 4) -> set[tuple]:
    return {tuple(words[i:i + k]) for i in range(max(1, len(words) - k + 1))}


def validate_duplicates(blocks: Sequence[dict], near_threshold: float = 0.85, min_words: int = 8) -> list[Finding]:
    out: list[Finding] = []
    items = [(b, _norm(_text_of(b))) for b in blocks if b.get("type") != "heading"]
    items = [(b, t) for b, t in items if len(t.split()) >= min_words]
    exact: dict[str, dict] = {}
    for b, t in items:
        key = f"{b.get('type')}|{t}"
        if key in exact:
            out.append(Finding("duplicate_block", "error", f"duplicate of block '{exact[key].get('id')}'", b.get("id")))
        else:
            exact[key] = b
    sh = [(b, _shingles(t.split())) for b, t in items]
    reported = {f.block_id for f in out}
    for i in range(len(sh)):
        for j in range(i + 1, len(sh)):
            (bi, si), (bj, sj) = sh[i], sh[j]
            if bj.get("id") in reported or bi.get("type") != bj.get("type"):
                continue
            inter = len(si & sj)
            if inter and inter / len(si | sj) >= near_threshold:
                out.append(Finding("near_duplicate_block", "warning", f"near-duplicate of block '{bi.get('id')}'", bj.get("id")))
                reported.add(bj.get("id"))
    return out


# ------------------------------------------------------------------ mermaid
_FLOW_EDGE = re.compile(r"(-->|---|-\.->|-\.-|==>|===|--[ox]|<-->)|--[^>\n]*-->")
_FLOW_NODE_LIMIT, _FLOW_EDGE_LIMIT = 60, 150


def validate_mermaid_structure(blocks: Sequence[dict]) -> list[Finding]:
    out: list[Finding] = []
    for b in blocks:
        if b.get("type") != "mermaid":
            continue
        bid, src = b.get("id"), b.get("source", "")
        lines = [l.rstrip() for l in src.strip().splitlines() if l.strip() and not l.strip().startswith("%%")]
        head = lines[0].split() if lines else []
        kind, body = (head[0] if head else ""), "\n".join(lines[1:])

        def err(code, msg):
            out.append(Finding(code, "error", msg, bid))

        if kind in ("flowchart", "graph"):
            if len(head) > 1 and head[1] not in ("TD", "TB", "BT", "RL", "LR"):
                err("mermaid_bad_direction", f"unknown direction '{head[1]}'")
            if not _FLOW_EDGE.search(body):
                err("mermaid_no_edges", "flowchart has no edges")
            edges = len(_FLOW_EDGE.findall(body))
            if edges > _FLOW_EDGE_LIMIT:
                err("mermaid_too_large", f"more than {_FLOW_EDGE_LIMIT} edges")
            ids = set(re.findall(r"^\s*([A-Za-z0-9_]+)\s*(?:[\[({>]|-->|---|==>|-\.)", body, re.M))
            if len(ids) > _FLOW_NODE_LIMIT:
                err("mermaid_too_large", f"more than {_FLOW_NODE_LIMIT} nodes")
            subs = len(re.findall(r"^\s*subgraph\b", body, re.M)); ends = len(re.findall(r"^\s*end\s*$", body, re.M))
            if subs != ends:
                err("mermaid_unbalanced_subgraph", "subgraph/end are not balanced")
        elif kind == "sequenceDiagram":
            if not re.search(r"->>|-->>|->|-->|-x|--x", body):
                err("mermaid_no_messages", "sequence diagram has no messages")
            opens = len(re.findall(r"^\s*(alt|opt|loop|par|critical|break|rect)\b", body, re.M))
            if opens != len(re.findall(r"^\s*end\s*$", body, re.M)):
                err("mermaid_unbalanced_blocks", "alt/opt/loop/par blocks are not closed with 'end'")
        elif kind in ("stateDiagram", "stateDiagram-v2"):
            if "-->" not in body:
                err("mermaid_no_transitions", "state diagram has no transitions")
        elif kind == "classDiagram":
            if not re.search(r"^\s*class\s+\w+|<\|--|\*--|o--|-->|\.\.>|--", body, re.M):
                err("mermaid_empty_class_diagram", "class diagram defines no classes or relations")
        elif kind == "erDiagram":
            if not re.search(r"\|\|--|}o--|--o\{|\}\|--|--\|\{|\|o--|--o\|", body):
                err("mermaid_no_relationships", "ER diagram has no relationships")
        elif kind == "pie":
            if not re.search(r'^\s*"[^"]+"\s*:\s*[\d.]+', body, re.M):
                err("mermaid_empty_pie", "pie chart has no data rows")
        elif kind == "gantt":
            if "section" not in body or "dateFormat" not in body:
                err("mermaid_gantt_incomplete", "gantt needs dateFormat and at least one section")
        elif kind == "mindmap":
            if len(lines) < 3:
                err("mermaid_mindmap_small", "mindmap needs a root and at least one child")
        elif kind == "journey":
            if not re.search(r"^\s*section\b", body, re.M):
                err("mermaid_journey_incomplete", "journey needs at least one section")
        quotes = src.count('"')
        if quotes % 2:
            err("mermaid_unbalanced_quotes", "odd number of double quotes")
    return out


# ------------------------------------------------------------------ code
def _balance(code: str) -> str | None:
    pairs, stack, i, n = {")": "(", "]": "[", "}": "{"}, [], 0, len(code)
    in_str, quote, esc, line_comment, block_comment = False, "", False, False, False
    while i < n:
        ch, nxt = code[i], code[i + 1] if i + 1 < n else ""
        if line_comment:
            line_comment = ch != "\n"
        elif block_comment:
            if ch == "*" and nxt == "/":
                block_comment = False; i += 1
        elif in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
        elif ch in "\"'`":
            in_str, quote = True, ch
        elif ch == "/" and nxt == "/":
            line_comment = True
        elif ch == "#":
            line_comment = True
        elif ch == "/" and nxt == "*":
            block_comment = True; i += 1
        elif ch in "([{":
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack.pop() != pairs[ch]:
                return f"unmatched '{ch}'"
        i += 1
    return f"unclosed '{stack[-1]}'" if stack else None


def validate_code(blocks: Sequence[dict]) -> list[Finding]:
    out: list[Finding] = []
    for b in blocks:
        if b.get("type") not in ("code", "algorithm"):
            continue
        bid = b.get("id")
        code = b.get("code") if b.get("type") == "code" else b.get("pseudocode", "")
        lang = (b.get("language") or "").lower()
        if not code:
            continue
        if code.lstrip().startswith("```") or code.rstrip().endswith("```"):
            out.append(Finding("code_contains_fence", "error", "code block still contains markdown fences", bid))
        if b.get("type") == "algorithm":
            continue
        if lang in ("python", "py"):
            try:
                ast.parse(code)
            except SyntaxError as e:
                out.append(Finding("code_syntax_error", "error", f"Python syntax error: {e.msg}", bid, f"line {e.lineno}"))
        elif lang == "json":
            try:
                json.loads(code)
            except ValueError as e:
                out.append(Finding("code_syntax_error", "error", f"invalid JSON: {e}", bid))
        elif lang in ("javascript", "js", "typescript", "ts", "java", "go", "rust", "c", "cpp", "c++", "csharp", "c#", "sql", "css", "bash", "sh"):
            problem = _balance(code)
            if problem:   # heuristic (no full parser): a warning, never an error
                out.append(Finding("code_unbalanced", "warning", f"possible syntax problem: {problem}", bid))
    return out


# ------------------------------------------------------------------ references
_REF = re.compile(r"(?<!\])\[(\d{1,3})\](?!\()")


def validate_references(blocks: Sequence[dict]) -> list[Finding]:
    out: list[Finding] = []
    citations = [b for b in blocks if b.get("type") == "citation"]
    labels = Counter(c.get("label", "").strip() for c in citations)
    for c in citations:
        lab = c.get("label", "").strip()
        if labels[lab] > 1:
            out.append(Finding("citation_label_duplicate", "error", f"citation label {lab} is used {labels[lab]} times", c.get("id")))
        try:
            validate_url(c.get("url", ""), resolve=False)       # syntax and literal-IP checks; no network access here
        except UnsafeURLError as e:
            out.append(Finding("citation_url_unsafe", "critical", f"citation URL rejected: {e.message}", c.get("id")))
    referenced: set[str] = set()
    for b in blocks:
        texts = [b.get("markdown", "")] if b.get("type") == "text" else [b.get("text", "")] if b.get("type") == "callout" else b.get("items", []) if b.get("type") == "list" else []
        for t in texts:
            for m in _REF.finditer(t):
                lab = f"[{m.group(1)}]"
                referenced.add(lab)
                if lab not in labels:
                    out.append(Finding("citation_missing", "error", f"reference {lab} has no matching citation block", b.get("id")))
    for c in citations:
        if c.get("label", "").strip() not in referenced:
            out.append(Finding("citation_unreferenced", "warning", f"citation {c.get('label')} is never referenced", c.get("id")))
    return out


# ------------------------------------------------------------------ structure / limits / accessibility
MAX_SECTION_CHARS = 60_000


def validate_structure(blocks: Sequence[dict]) -> list[Finding]:
    out: list[Finding] = []
    total = sum(len(json.dumps(b, ensure_ascii=False)) for b in blocks)
    if total > MAX_SECTION_CHARS:
        out.append(Finding("section_too_long", "error", f"section content is {total} characters (limit {MAX_SECTION_CHARS})"))
    prev = 1
    for b in blocks:
        if b.get("type") == "heading":
            lvl = b.get("level", 2)
            if lvl > prev + 1:
                out.append(Finding("heading_level_jump", "warning", f"heading level jumps from {prev} to {lvl}", b.get("id")))
            prev = lvl
        if b.get("type") == "table" and not any(h.strip() for h in b.get("headers", [])):
            out.append(Finding("table_no_headers", "error", "table has no header text", b.get("id")))
        if b.get("type") == "image" and not b.get("alt", "").strip():
            out.append(Finding("image_no_alt", "error", "image has no alt text", b.get("id")))
        if b.get("type") == "chart" and not (b.get("caption") or b.get("title")):
            out.append(Finding("chart_no_text_alternative", "warning", "chart has no caption or title (text alternative)", b.get("id")))
    return out
