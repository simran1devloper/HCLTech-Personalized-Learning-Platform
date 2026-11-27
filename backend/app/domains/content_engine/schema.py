"""Universal content block schema v2 (15 block types).

* Every block has a stable ``id``, a ``type``, an optional ``title`` and a validated payload.
* Model output may omit ``id``; ``assign_ids`` derives a deterministic one (so replays and single-block
  regeneration keep the same identity).
* Raw HTML and scripts are rejected everywhere except fields that are rendered strictly as literal text.
* Legacy v1 blocks (``blocks.py``) are accepted through ``legacy_to_v2``.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, ClassVar, Literal, Union
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from app.domains.content_engine.blocks import assert_safe_text, validate_mermaid
from app.domains.content_engine.latex_policy import lint_latex

SCHEMA_VERSION = "2.0"
BLOCK_TYPES = ("heading", "text", "list", "table", "callout", "mermaid", "math", "chart", "image", "code",
               "algorithm", "quiz", "flashcard", "exercise", "citation")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
MAX_CITATION_QUOTE = 300  # short excerpts only (copyright policy)


def _https_url(v: str) -> str:
    p = urlsplit(v.strip())
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise ValueError("url must be https without credentials")
    return v.strip()


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    #: fields rendered strictly as literal text (never HTML) and therefore allowed to contain markup characters
    _literal_fields: ClassVar[set[str]] = set()

    id: str
    title: str = Field("", max_length=200)
    meta: dict[str, Union[str, int, float, bool, None]] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id_ok(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("id must be 3-64 chars of letters, digits, '_' or '-'")
        return v

    @field_validator("meta")
    @classmethod
    def _meta_small(cls, v):
        if len(v) > 20 or any(len(str(x)) > 200 for x in v.values()):
            raise ValueError("meta too large")
        return v

    @model_validator(mode="after")
    def _no_unsafe_markup(self):
        for name in type(self).model_fields:
            if name in self._literal_fields or name in ("type", "id", "meta"):
                continue
            for text in _strings(getattr(self, name)):
                assert_safe_text(text)
        return self


def _strings(v: Any):
    if isinstance(v, str):
        yield v
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _strings(x)
    elif isinstance(v, BaseModel):
        for f in type(v).model_fields:
            yield from _strings(getattr(v, f))


class HeadingBlock(_Base):
    type: Literal["heading"] = "heading"
    level: int = Field(2, ge=1, le=4)
    text: str = Field(min_length=1, max_length=200)


class TextBlock(_Base):
    type: Literal["text"] = "text"
    markdown: str = Field(min_length=1, max_length=20000)


class ListBlock(_Base):
    type: Literal["list"] = "list"
    ordered: bool = False
    items: list[str] = Field(min_length=1, max_length=50)

    @field_validator("items")
    @classmethod
    def _item_len(cls, v):
        if any(not i.strip() or len(i) > 1000 for i in v):
            raise ValueError("list items must be non-empty and at most 1000 characters")
        return v


class TableBlock(_Base):
    type: Literal["table"] = "table"
    headers: list[str] = Field(min_length=1, max_length=12)
    rows: list[list[str]] = Field(max_length=100)
    caption: str = Field("", max_length=300)

    @model_validator(mode="after")
    def _rectangular(self):
        if any(len(r) != len(self.headers) for r in self.rows):
            raise ValueError("table rows must match header width")
        return self


class CalloutBlock(_Base):
    type: Literal["callout"] = "callout"
    variant: Literal["info", "tip", "warning", "danger", "note"] = "info"
    text: str = Field(min_length=1, max_length=2000)


class MermaidBlock(_Base):
    type: Literal["mermaid"] = "mermaid"
    source: str
    caption: str = Field("", max_length=300)

    @field_validator("source")
    @classmethod
    def _valid(cls, v):
        return validate_mermaid(v)


class MathBlock(_Base):
    type: Literal["math"] = "math"
    latex: str = Field(min_length=1, max_length=1000)
    display: bool = True
    variables: list[dict[str, str]] = Field(default_factory=list, max_length=20)  # [{"symbol": "x", "meaning": "..."}]
    _literal_fields: ClassVar[set[str]] = {"latex"}

    @field_validator("latex")
    @classmethod
    def _policy(cls, v):
        bad = [f for f in lint_latex(v) if f.severity in ("critical", "error")]
        if bad:
            raise ValueError(f"latex rejected: {bad[0].message}")
        return v


class ChartSeries(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    data: list[float] = Field(min_length=1, max_length=200)


class ChartBlock(_Base):
    type: Literal["chart"] = "chart"
    chart_type: Literal["bar", "line", "pie", "scatter"] = "bar"
    categories: list[str] = Field(min_length=2, max_length=200)
    series: list[ChartSeries] = Field(min_length=1, max_length=8)
    x_label: str = Field("", max_length=100)
    y_label: str = Field("", max_length=100)
    caption: str = Field("", max_length=300)
    source: str = Field("", max_length=300)   # where the numbers come from (meaningful data only)

    @model_validator(mode="after")
    def _aligned(self):
        if any(len(s.data) != len(self.categories) for s in self.series):
            raise ValueError("every series must have one value per category")
        return self


class ImageBlock(_Base):
    type: Literal["image"] = "image"
    url: str
    alt: str = Field(min_length=5, max_length=300)   # accessibility: alt text is mandatory
    caption: str = Field("", max_length=300)

    @field_validator("url")
    @classmethod
    def _https(cls, v):
        return _https_url(v)


class CodeBlock(_Base):
    type: Literal["code"] = "code"
    language: str = Field(pattern=r"^[a-zA-Z0-9_+#-]{1,20}$")
    code: str = Field(min_length=1, max_length=10000)
    filename: str = Field("", max_length=120)
    caption: str = Field("", max_length=300)
    _literal_fields: ClassVar[set[str]] = {"code"}   # code may legitimately contain markup; rendered as text only


class AlgorithmBlock(_Base):
    type: Literal["algorithm"] = "algorithm"
    name: str = Field(min_length=1, max_length=120)
    steps: list[str] = Field(min_length=1, max_length=40)
    time_complexity: str = Field("", max_length=60)
    space_complexity: str = Field("", max_length=60)
    pseudocode: str = Field("", max_length=5000)
    _literal_fields: ClassVar[set[str]] = {"pseudocode"}


class QuizBlock(_Base):
    type: Literal["quiz"] = "quiz"
    question_ids: list[str] = Field(min_length=1, max_length=50)


class FlashcardBlock(_Base):
    type: Literal["flashcard"] = "flashcard"
    front: str = Field(min_length=1, max_length=500)
    back: str = Field(min_length=1, max_length=1500)
    tags: list[str] = Field(default_factory=list, max_length=10)


class ExerciseBlock(_Base):
    type: Literal["exercise"] = "exercise"
    prompt: str = Field(min_length=1, max_length=3000)
    hints: list[str] = Field(default_factory=list, max_length=6)
    solution_outline: str = Field("", max_length=3000)
    difficulty: int | None = Field(None, ge=1, le=5)


class CitationBlock(_Base):
    type: Literal["citation"] = "citation"
    label: str = Field(min_length=1, max_length=20)          # e.g. "[1]"
    source_title: str = Field(min_length=1, max_length=300)
    url: str
    quote: str = Field("", max_length=MAX_CITATION_QUOTE)    # short excerpt only
    accessed_at: str = Field("", max_length=30)
    source_id: str = Field("", max_length=64)

    @field_validator("url")
    @classmethod
    def _https(cls, v):
        return _https_url(v)


ContentBlock = Annotated[Union[HeadingBlock, TextBlock, ListBlock, TableBlock, CalloutBlock, MermaidBlock, MathBlock,
                               ChartBlock, ImageBlock, CodeBlock, AlgorithmBlock, QuizBlock, FlashcardBlock,
                               ExerciseBlock, CitationBlock], Field(discriminator="type")]
_adapter: TypeAdapter = TypeAdapter(ContentBlock)


class SectionContent(BaseModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    section: str
    blocks: list[ContentBlock] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def _unique_ids(self):
        ids = [b.id for b in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("block ids must be unique within a section")
        return self


def validate_block(raw: dict) -> BaseModel:
    """Validate one block dict (must contain id). Raises pydantic.ValidationError."""
    return _adapter.validate_python(raw)


def make_block_id(section: str, ordinal: int, raw: dict) -> str:
    """Deterministic ID from section, position and content: identical regenerated blocks keep their identity."""
    body = {k: v for k, v in raw.items() if k not in ("id", "meta")}
    digest = hashlib.sha256(json.dumps([section, ordinal, body], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                            .encode()).hexdigest()[:12]
    return f"b_{ordinal:03d}_{digest}"


def assign_ids(section: str, blocks: list[dict]) -> list[dict]:
    return [b if b.get("id") else {**b, "id": make_block_id(section, i, b)} for i, b in enumerate(blocks)]


# ------------------------------------------------------------------ legacy v1 -> v2
def legacy_to_v2(section: str, block: dict) -> list[dict]:
    """Map one v1 block (``blocks.py``) to v2 blocks (ids not yet assigned)."""
    t = block.get("type")
    if t in ("text", "code", "table", "mermaid", "math", "quiz", "exercise"):
        return [{k: v for k, v in block.items()}]
    if t == "flashcards":
        return [{"type": "flashcard", "front": c["front"], "back": c["back"]} for c in block.get("cards", [])]
    if t == "case_study":
        out: list[dict] = [{"type": "heading", "level": 3, "text": block.get("title", "Case study")},
                           {"type": "text", "markdown": block.get("scenario", "")}]
        if block.get("questions"):
            out.append({"type": "list", "ordered": True, "items": list(block["questions"])})
        return out
    if t == "tutorial":
        return [{"type": "list", "ordered": True, "title": "Step by step", "items": list(block.get("steps", []))}]
    raise ValueError(f"unsupported legacy block type: {t!r}")
