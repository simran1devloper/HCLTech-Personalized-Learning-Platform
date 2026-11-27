"""Section contracts: what each of the nine sections must contain, as data (not prompt text).

A contract lists required *elements* (matched deterministically by heading text, block titles and leading bold labels
such as ``**Definition:**``), allowed block types, and size limits. Optional elements (math, charts, diagrams) are never
forced: the Content Architect decides whether a topic needs them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from app.domains.content_engine.findings import Finding

SECTION_IDS = ("summary", "key_concepts", "deep_dive", "real_world_applications", "interview_qa", "practical_guide",
               "learning_roadmap", "flashcards", "recommendations")
LEGACY_ALIASES = {"viva_voice": "interview_qa", "best_practices": "practical_guide"}   # old section ids -> v2 ids

_BOLD_LABEL = re.compile(r"(?:\*\*|__)\s*([^*_\n]{2,60}?)\s*:?\s*(?:\*\*|__)")


@dataclass(frozen=True)
class Element:
    id: str
    description: str
    keywords: tuple[str, ...] = ()
    block_types: tuple[str, ...] = ()   # if set, a block of one of these types must also be in the element's scope
    optional: bool = False


@dataclass(frozen=True)
class SectionContract:
    id: str
    title: str
    token_budget: int
    allowed_blocks: frozenset[str]
    elements: tuple[Element, ...]
    min_blocks: int = 3
    max_blocks: int = 80
    # per-scope rules: every heading of `scope_level` opens a scope that must contain `scope_elements`
    scope_level: int | None = None
    min_scopes: int = 0
    scope_elements: tuple[Element, ...] = ()
    must_include_types: tuple[str, ...] = ()   # block types that must appear at least once (e.g. code in the guide)
    min_of_type: dict[str, int] = field(default_factory=dict)


@dataclass
class ContractReport:
    section: str
    missing: list[str] = field(default_factory=list)          # required element ids not found
    findings: list[Finding] = field(default_factory=list)
    required_total: int = 0
    required_met: int = 0

    @property
    def coverage(self) -> float:
        return 1.0 if self.required_total == 0 else self.required_met / self.required_total

    @property
    def complete(self) -> bool:
        return not self.missing and not [f for f in self.findings if f.severity in ("critical", "error")]


# ----------------------------------------------------------------------------- matching
def _g(block) -> dict:
    return block if isinstance(block, dict) else block.model_dump()


def _labels(block: dict) -> list[str]:
    """Text a block 'announces' about itself: heading text, title, and leading bold labels in text-like fields."""
    out = []
    if block.get("type") == "heading":
        out.append(block.get("text", ""))
    if block.get("title"):
        out.append(block["title"])
    for key in ("markdown", "text"):
        if isinstance(block.get(key), str) and block.get("type") != "heading":
            out += _BOLD_LABEL.findall(block[key])
    for item in block.get("items", []) if block.get("type") == "list" else []:
        out += _BOLD_LABEL.findall(item)
    return [x.lower() for x in out if x]


def _matches(labels: Iterable[str], keywords: Sequence[str]) -> bool:
    return any(k in lab for lab in labels for k in keywords)


def _scopes(blocks: list[dict], level: int | None) -> list[tuple[dict | None, list[dict]]]:
    """Split into (heading, blocks-until-next-heading-of-same-or-higher-level). Level None => one scope = everything."""
    if level is None:
        return [(None, blocks)]
    out, cur_head, cur = [], None, []
    for b in blocks:
        if b.get("type") == "heading" and b.get("level", 2) <= level:
            if cur_head is not None:
                out.append((cur_head, cur))
            cur_head, cur = (b if b.get("level") == level else None), []
            if cur_head is None:
                continue
        elif cur_head is not None:
            cur.append(b)
    if cur_head is not None:
        out.append((cur_head, cur))
    return out


def _satisfied(el: Element, blocks: list[dict]) -> bool:
    types_ok = lambda bs: not el.block_types or any(b.get("type") in el.block_types for b in bs)
    # 1) a heading (any level) whose text matches, with its section content satisfying block_types
    for i, b in enumerate(blocks):
        if b.get("type") == "heading" and _matches(_labels(b), el.keywords) and el.keywords:
            lvl = b.get("level", 2)
            scope = [b]
            for nb in blocks[i + 1:]:
                if nb.get("type") == "heading" and nb.get("level", 2) <= lvl:
                    break
                scope.append(nb)
            if types_ok(scope):
                return True
    # 2) any block title / bold label matching, with that block satisfying block_types (or block_types unset)
    for b in blocks:
        if el.keywords and _matches(_labels(b), el.keywords) and (not el.block_types or b.get("type") in el.block_types or types_ok([b])):
            return True
    # 3) no keywords: satisfied by presence of the block type alone
    return not el.keywords and bool(el.block_types) and types_ok(blocks)


# ----------------------------------------------------------------------------- validation
def validate_section(section: str, blocks: Sequence) -> ContractReport:
    section = LEGACY_ALIASES.get(section, section)
    contract = CONTRACTS[section]
    bl = [_g(b) for b in blocks]
    rep = ContractReport(section)

    def add(code, sev, msg, block_id=None):
        rep.findings.append(Finding(code, sev, msg, block_id))

    if len(bl) < contract.min_blocks:
        add("contract_too_few_blocks", "error", f"{section} needs at least {contract.min_blocks} blocks, has {len(bl)}")
    if len(bl) > contract.max_blocks:
        add("contract_too_many_blocks", "error", f"{section} allows at most {contract.max_blocks} blocks, has {len(bl)}")
    for b in bl:
        if b.get("type") not in contract.allowed_blocks:
            add("contract_block_not_allowed", "error", f"block type '{b.get('type')}' is not allowed in {section}", b.get("id"))
    for t in contract.must_include_types:
        if not any(b.get("type") == t for b in bl):
            add("contract_missing_block_type", "error", f"{section} must include at least one '{t}' block")
    for t, n in contract.min_of_type.items():
        if sum(b.get("type") == t for b in bl) < n:
            add("contract_too_few_of_type", "error", f"{section} needs at least {n} '{t}' blocks")

    for el in contract.elements:
        if el.optional:
            continue
        rep.required_total += 1
        if _satisfied(el, bl):
            rep.required_met += 1
        else:
            rep.missing.append(el.id)
            add("contract_missing_element", "error", f"missing required element: {el.description}")

    if contract.scope_level is not None:
        scopes = _scopes(bl, contract.scope_level)
        if len(scopes) < contract.min_scopes:
            add("contract_too_few_scopes", "error",
                f"{section} needs at least {contract.min_scopes} sections at heading level {contract.scope_level}, has {len(scopes)}")
        for head, content in scopes:
            for el in contract.scope_elements:
                if el.optional:
                    continue
                rep.required_total += 1
                if _satisfied(el, content):
                    rep.required_met += 1
                else:
                    name = (head or {}).get("text", "?")
                    rep.missing.append(f"{el.id}@{name}")
                    add("contract_missing_element", "error", f"'{name}' is missing: {el.description}", (head or {}).get("id"))
    return rep


# ----------------------------------------------------------------------------- the nine contracts
def _E(id, desc, kw=(), types=(), optional=False) -> Element:
    return Element(id, desc, tuple(k.lower() for k in kw), tuple(types), optional)


_TEXTY = {"heading", "text", "list", "callout", "table", "citation", "image"}
_TECH = _TEXTY | {"code", "algorithm", "math", "mermaid", "chart"}

CONTRACTS: dict[str, SectionContract] = {c.id: c for c in (
    SectionContract("summary", "Summary", 700, frozenset(_TEXTY | {"mermaid", "math"}), (
        _E("definition", "definition of the topic", ("definition", "what is", "overview")),
        _E("importance", "why the topic matters", ("importance", "why it matters", "why")),
        _E("objectives", "learning objectives", ("learning objectives", "objectives", "you will learn"), ("list", "text")),
        _E("prerequisites", "prerequisites", ("prerequisite", "before you start")),
        _E("workflow", "high-level workflow", ("workflow", "how it works", "high-level", "process")),
        _E("topic_map", "topic map of related areas", ("topic map", "concept map", "map of")),
    ), min_blocks=6),
    SectionContract("key_concepts", "Key Concepts", 900, frozenset(_TECH), (), min_blocks=6, scope_level=3, min_scopes=3, scope_elements=(
        _E("definition", "a definition", ("definition",)),
        _E("intuition", "an intuition", ("intuition",)),
        _E("internal_working", "how it works internally", ("internal working", "how it works", "internals", "internal")),
        _E("example", "an example", ("example",)),
        _E("related", "related concepts", ("related",)),
    )),
    SectionContract("deep_dive", "Deep Dive", 1600, frozenset(_TECH), (
        _E("architecture", "internal architecture", ("architecture", "internal")),
        _E("execution_flow", "execution flow", ("execution flow", "flow", "lifecycle", "request path")),
        _E("algorithms", "algorithms", ("algorithm",)),
        _E("data_structures", "data structures", ("data structure",)),
        _E("math_foundation", "mathematical foundation", ("mathematical foundation", "mathematics", "math"), optional=True),
        _E("complexity", "complexity and performance", ("complexity", "performance")),
        _E("trade_offs", "trade-offs", ("trade-off", "trade off", "tradeoff")),
    ), min_blocks=8),
    SectionContract("real_world_applications", "Real-world Applications", 900, frozenset(_TEXTY | {"mermaid", "chart"}), (
        _E("industry_applications", "industry applications", ("application", "industry", "use case")),
        _E("production_scenarios", "production scenarios", ("production", "scenario")),
        _E("challenges", "engineering challenges", ("challenge",)),
        _E("failure_handling", "failure handling", ("failure",)),
    ), min_blocks=5),
    SectionContract("interview_qa", "Interview Q&A", 1200, frozenset(_TEXTY | {"code"}), (
        _E("beginner", "beginner questions", ("beginner", "easy", "fundamental")),
        _E("intermediate", "intermediate questions", ("intermediate", "medium")),
        _E("advanced", "advanced questions", ("advanced", "hard")),
        _E("scenario", "scenario-based questions", ("scenario",)),
        _E("reasoning", "technical reasoning questions", ("reasoning", "why", "trade-off")),
        _E("follow_ups", "follow-up questions", ("follow-up", "follow up")),
    ), min_blocks=8),
    SectionContract("practical_guide", "Practical Implementation Guide", 2000, frozenset(_TECH), (
        _E("problem", "problem definition", ("problem",)),
        _E("requirements", "requirements", ("requirement",)),
        _E("technology_selection", "technology selection", ("technology", "tech stack", "stack")),
        _E("architecture", "architecture", ("architecture", "design")),
        _E("components", "component responsibilities", ("component", "responsibilit")),
        _E("project_structure", "project structure", ("project structure", "folder", "layout", "structure")),
        _E("steps", "step-by-step implementation", ("step-by-step", "step by step", "implementation", "steps")),
        _E("apis_data_models", "APIs and data models", ("api", "data model", "schema")),
        _E("testing", "testing", ("testing", "tests")),
        _E("debugging", "debugging", ("debugging", "troubleshoot")),
        _E("performance", "performance", ("performance", "optimi")),
        _E("deployment", "deployment", ("deployment", "deploy")),
    ), min_blocks=14, must_include_types=("code",)),
    SectionContract("learning_roadmap", "Learning Roadmap", 800, frozenset(_TEXTY | {"mermaid"}), (
        _E("beginner", "beginner stage", ("beginner",)),
        _E("intermediate", "intermediate stage", ("intermediate",)),
        _E("advanced", "advanced stage", ("advanced",)),
        _E("dependencies", "dependencies between stages", ("dependenc", "prerequisite")),
        _E("milestones", "practice milestones", ("milestone", "practice")),
    ), min_blocks=6),
    SectionContract("flashcards", "Flashcards", 800, frozenset({"heading", "flashcard", "math"}), (),
                    min_blocks=5, max_blocks=40, min_of_type={"flashcard": 5}),
    SectionContract("recommendations", "Recommendations", 700, frozenset(_TEXTY), (
        _E("gaps", "knowledge gaps", ("gap", "weak")),
        _E("next_topics", "next topics", ("next topic", "next")),
        _E("practice", "recommended practice", ("practice",)),
    ), min_blocks=4),
)}
assert tuple(CONTRACTS) == SECTION_IDS
