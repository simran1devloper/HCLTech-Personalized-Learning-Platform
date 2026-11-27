"""Pydantic contracts for the JSON sections of a study guide (single source of truth).

* The same models validate model output AND generate the JSON Schema sent to providers for constrained decoding.
* Leniency lives in the models (coercion of nested / mis-typed values, dropping unusable list items) so that the
  parser stays a thin ``extract -> validate`` step. Violations raise ``SchemaViolation`` whose ``errors`` are short,
  human-readable lines that are fed back to the model by the repair retry.
* Output shapes are unchanged from the previous hand-written parser (``{term, definition}``, ``{question, answer}``,
  ``{weak_areas, next_topics[{title, reason}], practice_suggestions, resources[{title, description}]}``).
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
from typing import Any, Dict, List, Type

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class SchemaViolation(ValueError):
    """Model output did not satisfy the section schema. ``errors`` is safe to show to the model (no raw output)."""

    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__("; ".join(errors) or "invalid output")


# ----------------------------------------------------------------------------------------------- coercion
def coerce_text(v: Any) -> str:
    """Readable text for any value. Never produces '[object Object]' or a Python/JSON repr of a container."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (int, float, bool)):
        return str(v)
    if isinstance(v, dict):
        for k in ("title", "name", "term", "area", "topic", "text", "description"):  # prefer the conventional fields
            if isinstance(v.get(k), str) and v[k].strip():
                rest = [f"{kk}: {coerce_text(vv)}" for kk, vv in v.items() if kk != k and coerce_text(vv)]
                return v[k].strip() + (f" — {'; '.join(rest)}" if rest else "")
        return "; ".join(f"{k}: {coerce_text(x)}" for k, x in v.items() if coerce_text(x))
    if isinstance(v, (list, tuple)):
        return ", ".join(t for t in (coerce_text(x) for x in v) if t)
    return str(v)


def _items(data: Any, key: str) -> list:
    """Accept {"key": [...]} or a bare [...]."""
    items = data.get(key) if isinstance(data, dict) else data
    return items if isinstance(items, list) else []


# ----------------------------------------------------------------------------------------------- key concepts
class KeyConcept(BaseModel):
    term: str
    definition: str

    @field_validator("term", "definition", mode="before")
    @classmethod
    def _text(cls, v: Any) -> str:
        return coerce_text(v)

    @field_validator("term", "definition")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("must not be empty")
        return v


class KeyConcepts(BaseModel):
    key_concepts: List[KeyConcept] = Field(min_length=3, max_length=8)

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, data: Any) -> Any:
        keep = [i for i in _items(data, "key_concepts")
                if isinstance(i, dict) and coerce_text(i.get("term")) and coerce_text(i.get("definition"))]
        return {"key_concepts": keep[:8]}


# ----------------------------------------------------------------------------------------------- flashcards
class Flashcard(BaseModel):
    question: str
    answer: str

    @field_validator("question", "answer", mode="before")
    @classmethod
    def _text(cls, v: Any) -> str:
        return coerce_text(v)

    @field_validator("question", "answer")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("must not be empty")
        return v


class Flashcards(BaseModel):
    flashcards: List[Flashcard] = Field(min_length=3, max_length=10)

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, data: Any) -> Any:
        keep = [i for i in _items(data, "flashcards")
                if isinstance(i, dict) and coerce_text(i.get("question")) and coerce_text(i.get("answer"))]
        return {"flashcards": keep[:10]}


# ----------------------------------------------------------------------------------------------- recommendations
class NextTopic(BaseModel):
    title: str
    reason: str = ""


class Resource(BaseModel):
    title: str
    description: str = ""


def normalise_recommendations(rec: dict) -> dict:
    """Lists of plain strings for weak areas/practice; objects {title, reason|description} for topics/resources."""
    def strings(items):
        return [t for t in (coerce_text(i) for i in (items if isinstance(items, list) else [])) if t]

    def objects(items, second):
        out = []
        for i in items if isinstance(items, list) else []:
            if isinstance(i, dict):
                title = coerce_text(i.get("title") or i.get("name") or i.get("topic"))
                other = coerce_text(i.get(second) or i.get("description") or i.get("reason"))
            else:
                title, other = coerce_text(i), ""
            if title:
                out.append({"title": title, second: other})
        return out
    return {"weak_areas": strings(rec.get("weak_areas")), "practice_suggestions": strings(rec.get("practice_suggestions")),
            "next_topics": objects(rec.get("next_topics"), "reason"), "resources": objects(rec.get("resources"), "description")}


class Recommendations(BaseModel):
    weak_areas: List[str] = Field(default_factory=list)
    next_topics: List[NextTopic] = Field(default_factory=list)
    practice_suggestions: List[str] = Field(default_factory=list)
    resources: List[Resource] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError("must be a JSON object with weak_areas, next_topics, practice_suggestions and resources")
        return normalise_recommendations(data)

    @model_validator(mode="after")
    def _not_all_empty(self) -> "Recommendations":
        if not (self.weak_areas or self.next_topics or self.practice_suggestions or self.resources):
            raise ValueError("at least one recommendation list must be filled in")
        return self


SECTION_MODELS: Dict[str, Type[BaseModel]] = {
    "key_concepts": KeyConcepts, "flashcards": Flashcards, "recommendations": Recommendations,
}


ITEM_MODELS: Dict[str, Type[BaseModel]] = {"key_concepts": KeyConcept, "flashcards": Flashcard}  # sections whose items can stream


def validate_item(name: str, obj: Any) -> Any:
    """One streamed list item as the plain dict stored in the guide, or None if it is not (yet) usable."""
    try:
        return ITEM_MODELS[name].model_validate(obj).model_dump()
    except (ValidationError, KeyError):
        return None


# ----------------------------------------------------------------------------------------------- validation
def validate_section(name: str, data: Any) -> Any:
    """Validate parsed JSON against the section model and return the plain value stored in the guide."""
    model = SECTION_MODELS[name]
    try:
        obj = model.model_validate(data)
    except ValidationError as e:
        raise SchemaViolation(_describe_errors(e)) from e
    dumped = obj.model_dump()
    return dumped[name] if name in ("key_concepts", "flashcards") else dumped


def _describe_errors(e: ValidationError) -> List[str]:
    out: List[str] = []
    for x in e.errors()[:8]:
        loc = ".".join(str(p) for p in x["loc"]) or "output"
        out.append(f"{loc}: {x['msg'].removeprefix('Value error, ')}")
    return out


# ----------------------------------------------------------------------------------------------- provider schemas
def _inline(node: Any, defs: dict) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline(copy.deepcopy(defs[node["$ref"].rsplit("/", 1)[-1]]), defs)
        out = {}
        for k, v in node.items():
            if k in ("$defs", "title", "default", "description"):  # schema keywords (a *field* may legitimately be called "title")
                continue
            out[k] = {n: _inline(sub, defs) for n, sub in v.items()} if k == "properties" else _inline(v, defs)
        if out.get("type") == "object" and "properties" in out:
            out["required"] = list(out["properties"])  # constrained decoding must not drop fields
        return out
    if isinstance(node, list):
        return [_inline(x, defs) for x in node]
    return node


@functools.lru_cache(maxsize=None)
def _provider_schema_json(name: str) -> str:
    raw = SECTION_MODELS[name].model_json_schema()
    return json.dumps(_inline(raw, raw.get("$defs", {})), sort_keys=True)


def provider_schema(name: str) -> dict:
    """Self-contained JSON Schema (no $ref/$defs/titles) accepted by Ollama `format` and Gemini `responseSchema`."""
    return json.loads(_provider_schema_json(name))


def schema_fingerprint() -> str:
    return hashlib.sha256("|".join(_provider_schema_json(n) for n in sorted(SECTION_MODELS)).encode()).hexdigest()[:8]
