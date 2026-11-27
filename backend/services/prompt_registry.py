"""Versioned prompt registry for study-guide sections.

* Every section has its own semantic ``version`` (bump major/minor when the prompt changes what is produced).
* Prompts are laid out as **stable prefix -> section instructions -> request-specific tail**, so the long constant part
  is identical across requests (friendly to provider prompt caching) and only the short tail varies.
* No redundant boilerplate: shared rules live once in ``PREFIX``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, Optional

from app.domains.content_engine import study_schemas

PREFIX = ("You write one section of a technical study guide. Be accurate and concrete; never invent facts, APIs, numbers "
          "or sources. If you are unsure, say so briefly. Use plain Markdown only: no HTML, no scripts. "
          "Flag pitfalls as '> **Warning:** ...', recommended practice as '> **Best practice:** ...' and helpful hints as '> **Tip:** ...' (sparingly).")

DEPTH = {
    "Beginner": "plain language, analogies and step-by-step explanations; define every technical term",
    "Intermediate": "assume the basics; use correct terminology with brief context; explain mechanics and design decisions",
    "Expert": "precise technical language; focus on internals, edge cases, performance trade-offs and advanced patterns",
}

_FORMAT = {
    "markdown": "Output ONLY the Markdown for this section: no preamble, no closing remarks, no JSON, and do not wrap the whole answer in a code fence.",
    "json": "Output ONLY valid JSON matching the schema: no Markdown, no commentary.",
    "text": "Output ONLY the text.",
}


@dataclass(frozen=True)
class SectionPrompt:
    section: str
    version: str
    kind: str          # markdown | json | text
    max_tokens: int
    instructions: str


_P = [
    SectionPrompt("summary", "1.0.0", "markdown", 700,
                  "Write a 3-4 paragraph overview: what the topic is, why it matters, where it fits, and what the learner will gain. "
                  "Use **bold** for key terms and `inline code` where useful."),
    SectionPrompt("key_concepts", "1.0.1", "json", 900,
                  'Return JSON: {"key_concepts": [{"term": "...", "definition": "..."}]} with exactly 6 concepts. '
                  "Each definition: what it is, how it works, and a one-sentence example."),
    SectionPrompt("deep_dive", "1.1.0", "markdown", 1600,
                  "Write a technical deep dive with 5-7 '## ' sections covering core mechanics, how it works internally, "
                  "performance characteristics and one worked example with a fenced code block."),
    SectionPrompt("real_world_applications", "1.0.0", "markdown", 900,
                  "Describe 5 concrete real-world uses. For each: a '## ' heading naming the domain, 2 sentences on the "
                  "problem solved, and a specific example. Finish with a short '## Common pitfalls' section."),
    SectionPrompt("viva_voice", "1.0.1", "markdown", 1200,
                  "Write 6 interview questions. For each: '### Q: ...', then '**Answer:** ...' (3-5 sentences), then "
                  "'**Follow-up:** ...'. Mix conceptual, scenario and design questions; tag each 🟢 Easy / 🟡 Medium / 🔴 Hard."),
    SectionPrompt("practical_guide", "1.1.0", "markdown", 1800,
                  "Write a practical implementation guide for building something real with this topic. Use these '## ' headings in order: "
                  "Problem, Requirements, Technology selection, Architecture, Component responsibilities, Project structure (a short tree in a "
                  "fenced block), Step-by-step implementation (numbered steps, each with a short fenced code example), APIs and data models, "
                  "Testing, Debugging, Performance, Deployment. Keep each part concise and specific."),
    SectionPrompt("learning_roadmap", "1.1.0", "markdown", 800,
                  "Write a learning roadmap with '## Phase 1', '## Phase 2', '## Phase 3' (Foundation -> Intermediate -> "
                  "Advanced): sub-topics per phase, estimated time per phase, and 3 kinds of resources to use."),
    SectionPrompt("flashcards", "1.0.1", "json", 800,
                  'Return JSON: {"flashcards": [{"question": "...", "answer": "..."}]} with exactly 8 cards. '
                  "Questions test understanding; answers are precise and short."),
    SectionPrompt("recommendations", "1.0.1", "json", 700,
                  'Return JSON: {"weak_areas": [2 strings], "next_topics": [3 objects {"title", "reason"}], '
                  '"practice_suggestions": [4 strings], "resources": [3 objects {"title", "description"}]}. '
                  "Base weak areas on the diagnostic score."),
    SectionPrompt("agent_intro", "1.0.0", "text", 160,
                  "Write a warm 2-3 sentence greeting from the AI tutor that mentions the learner's level and score, names "
                  "1-2 areas to work on, and invites their first question. Plain text only."),
]
SECTION_PROMPTS: Dict[str, SectionPrompt] = {p.section: p for p in _P}

# Time-budget planning: core sections are generated first; optional ones are deferred ("generate on demand") when time is short.
CORE_SECTIONS = ("summary", "key_concepts", "flashcards", "deep_dive", "recommendations", "agent_intro")
OPTIONAL_SECTIONS = ("real_world_applications", "viva_voice", "practical_guide", "learning_roadmap")
# Smallest useful output when a markdown section has to be shortened to fit the budget (JSON/text sections are never shortened).
MIN_TOKENS = {"summary": 300, "deep_dive": 600, "real_world_applications": 350, "viva_voice": 500, "practical_guide": 700, "learning_roadmap": 350}
# Typical output is well below the max_tokens cap; planning uses this share of the cap.
EXPECTED_FRACTION = 0.65

# Deterministic icon tokens (Lucide names). Never generated by the model, so they can never be invented or broken.
SECTION_ICONS: Dict[str, str] = {
    "summary": "file-text", "key_concepts": "key-round", "deep_dive": "microscope", "real_world_applications": "globe",
    "viva_voice": "mic", "practical_guide": "wrench", "learning_roadmap": "map", "flashcards": "layers",
    "recommendations": "target", "agent_intro": "bot",
}


def section_icon(section: str) -> str:
    return SECTION_ICONS.get(section, "file-text")

# ------------------------------------------------------------------------------------------------ few-shot examples
@dataclass(frozen=True)
class Example:
    topic: str   # deliberately unrelated to typical study topics; the example is dropped if the learner picks it anyway
    output: str  # exactly what a good answer looks like (JSON examples are validated against the section schema in tests)


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


FEW_SHOT: Dict[str, tuple] = {
    "key_concepts": (Example("HTTP caching", _j({"key_concepts": [
        {"term": "ETag", "definition": "A validator token the server attaches to a response. Clients send it back in If-None-Match so the server can answer 304 instead of resending the body, e.g. ETag: \"v42\"."},
        {"term": "Cache-Control", "definition": "A response header that tells caches how long and where a response may be stored. For example max-age=3600 lets a browser reuse it for an hour."},
        {"term": "Stale-while-revalidate", "definition": "A directive that lets a cache serve an expired response immediately while it refreshes it in the background, hiding revalidation latency from the user."}]})),),
    "flashcards": (Example("SQL indexes", _j({"flashcards": [
        {"question": "Why can an index slow down INSERT statements?", "answer": "Every index on the table must be updated for each new row."},
        {"question": "What does a composite index on (a, b) NOT help with?", "answer": "Queries that filter only on b, because the index is ordered by a first."},
        {"question": "When is a full table scan faster than an index lookup?", "answer": "When the query returns a large fraction of the rows."}]})),),
    "recommendations": (Example("Docker networking", _j({
        "weak_areas": ["Container DNS resolution", "Port publishing vs exposing"],
        "next_topics": [{"title": "User-defined bridge networks", "reason": "They add automatic service discovery between containers."},
                        {"title": "Overlay networks", "reason": "Needed once containers span several hosts."}],
        "practice_suggestions": ["Run two containers on one user-defined network and reach one by name.", "Publish a port and inspect the iptables rules it creates."],
        "resources": [{"title": "Docker networking overview", "description": "Official guide to the built-in network drivers."},
                      {"title": "Play with Docker", "description": "Free browser sandbox for network experiments."}]})),),
    "viva_voice": (Example("REST APIs", "### Q: What makes an HTTP method idempotent? 🟢 Easy\n"
                           "**Answer:** A method is idempotent when repeating the same request leaves the server in the same state as sending it once. "
                           "GET, PUT and DELETE are idempotent; POST generally is not. This is why clients can safely retry a timed-out PUT.\n"
                           "**Follow-up:** How would you make a POST endpoint safe to retry?"),),
}
FEWSHOT_HASH = hashlib.sha256(_j({k: [(e.topic, e.output) for e in v] for k, v in sorted(FEW_SHOT.items())}).encode()).hexdigest()[:8]


def render_examples(section: str, topic: str = "") -> str:
    ex = [e for e in FEW_SHOT.get(section, ()) if e.topic.lower() not in (topic or "").lower() and (topic or "").lower() not in e.topic.lower() or not topic]
    return "\n".join(f"EXAMPLE (topic: {e.topic}; shortened, so follow the counts above rather than this length):\n{e.output}\nEND EXAMPLE" for e in ex)


# ------------------------------------------------------------------------------------------------ Mermaid diagrams
MERMAID_SECTIONS = ("deep_dive", "practical_guide", "learning_roadmap")
MERMAID_RULES = ("Include exactly one diagram in a ```mermaid fenced block (flowchart TD or sequenceDiagram, at most 12 nodes). "
                 'Put every node label in double quotes, with no parentheses, quotes or HTML inside, and one statement per line. Example:\n'
                 '```mermaid\nflowchart TD\n    A["Client request"] --> B["Cache lookup"]\n    B -->|hit| C["Return cached copy"]\n'
                 '    B -->|miss| D["Fetch from origin"]\n```')


def _flag(name: str) -> bool:
    from app.core.config import get_settings
    return bool(getattr(get_settings(), name))


def prompt_version(section: str) -> str:
    return SECTION_PROMPTS[section].version


def guide_prompt_fingerprint() -> str:
    """Hash of every section id + version. Used in the whole-guide cache key (per-section keys arrive in Iteration 2)."""
    raw = ("|".join(f"{s}:{p.version}" for s, p in sorted(SECTION_PROMPTS.items())) + "|schemas:" + study_schemas.schema_fingerprint()
           + f"|fewshot:{FEWSHOT_HASH if _flag('CONTENT_FEWSHOT') else 'off'}|mermaid:{'on' if _flag('CONTENT_MERMAID') else 'off'}")
    return "g" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def section_schema(section: str) -> Optional[dict]:
    """JSON Schema for a json section (sent to providers for constrained decoding); None for markdown/text sections."""
    return study_schemas.provider_schema(section) if section in study_schemas.SECTION_MODELS else None


REPAIR_MAX_ECHO = 1500


def build_repair_prompt(original: str, previous: str, errors: list[str], kind: str = "json") -> str:
    """Original prompt + what was wrong with the previous answer, so the retry fixes the defect instead of rolling the dice.
    Markdown answers are long, so they are not echoed back: the model is told what to fix and rewrites the section."""
    lines = "\n".join(f"- {e}" for e in errors[:8]) or "- the output was not valid JSON"
    if kind != "json":
        return f"{original}\n---\nYour previous answer was rejected:\n{lines}\nWrite the complete section again and fix every problem above."
    prev = previous.strip()
    if len(prev) > REPAIR_MAX_ECHO:
        prev = prev[:REPAIR_MAX_ECHO] + " …"
    return (f"{original}\n---\nYour previous answer was rejected:\n{lines}\n"
            f"Previous answer:\n{prev}\n"
            "Return ONLY the corrected JSON object that fixes every problem above. No commentary, no Markdown.")


def stable_prefix(section: str, topic: str = "") -> str:
    """The constant part of the prompt (identical for every request of this section; examples are dropped only if the
    learner's topic is the example's own topic)."""
    p = SECTION_PROMPTS[section]
    parts = [PREFIX, f"SECTION: {section}", p.instructions]
    if section in MERMAID_SECTIONS and _flag("CONTENT_MERMAID"):
        parts.append(MERMAID_RULES)
    parts.append(_FORMAT[p.kind])
    if _flag("CONTENT_FEWSHOT") and (ex := render_examples(section, topic)):
        parts.append(ex)
    return "\n".join(parts)


def build_section_prompt(section: str, topic: str, level: str, score: int) -> str:
    """Stable prefix first, request-specific tail last."""
    tail = (f"\n---\nTopic: {topic}\nLearner level: {level} (diagnostic score {score}/10)\n"
            f"Depth: {DEPTH.get(level, DEPTH['Intermediate'])}")
    return stable_prefix(section, topic) + tail


def prompt_token_estimate(text: str) -> int:
    """Pessimistic estimate (3 characters per token) used only for budgeting and tests."""
    return max(1, len(text) // 3)
