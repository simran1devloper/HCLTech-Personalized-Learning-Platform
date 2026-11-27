"""Incremental block parser for model output framed as JSON Lines (ADR 0011).

The model emits one block (JSON object) per line; models also pretty-print, wrap output in fences or prose, or add
commas, so the parser is a character-level state machine that extracts every *top-level JSON object* regardless of how
it is laid out. Properties:

* **Chunk-boundary independent**: feeding the same text in any chunking yields identical results.
* **One bad block never discards another**: each object is parsed, repaired (deterministically), validated and
  reported on its own; failed objects still consume an ordinal so block ids stay stable when only that block is
  regenerated later.
* Deterministic repair only (trailing commas, raw newlines inside strings); nothing is guessed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Union

from pydantic import BaseModel, ValidationError

from app.domains.content_engine.schema import make_block_id, validate_block

_OUTSIDE, _MAYBE, _INSIDE = 0, 1, 2
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


@dataclass
class ParsedBlock:
    ordinal: int
    block: BaseModel
    raw: dict
    repaired: bool = False


@dataclass
class BlockError:
    ordinal: int
    stage: str      # "syntax" | "schema" | "limit" | "truncated"
    message: str
    snippet: str    # first 200 characters of the offending object (for logs only)


Item = Union[ParsedBlock, BlockError]


def _escape_raw_newlines_in_strings(text: str) -> str:
    out, in_str, esc = [], False, False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            elif ch == "\n":
                out.append("\\n"); continue
            elif ch == "\r":
                continue
            elif ch == "\t":
                out.append("\\t"); continue
        elif ch == '"':
            in_str = True
        out.append(ch)
    return "".join(out)


def repair_and_load(text: str) -> tuple[object, bool]:
    """Returns (value, repaired). Raises ValueError if not recoverable with the deterministic repairs."""
    try:
        return json.loads(text), False
    except ValueError:
        pass
    candidate = text
    for fix in (lambda t: _TRAILING_COMMA.sub(r"\1", t), _escape_raw_newlines_in_strings,
                lambda t: _TRAILING_COMMA.sub(r"\1", _escape_raw_newlines_in_strings(t))):
        candidate = fix(text)
        try:
            return json.loads(candidate), True
        except ValueError:
            continue
    raise ValueError("invalid JSON that deterministic repair cannot fix")


class BlockStreamParser:
    def __init__(self, section: str, max_object_chars: int = 50_000, max_blocks: int = 200):
        self.section, self.max_object_chars, self.max_blocks = section, max_object_chars, max_blocks
        self._state = _OUTSIDE
        self._buf: list[str] = []
        self._size = 0
        self._depth = 0
        self._in_str = False
        self._esc = False
        self._oversize = False
        self._limit_reported = False
        self.ordinal = 0                # number of objects seen so far (including failed ones)
        self.stats = {"blocks": 0, "errors": 0, "repaired": 0, "noise_chars": 0}

    # ------------------------------------------------------------------ public
    def feed(self, chunk: str) -> list[Item]:
        out: list[Item] = []
        for ch in chunk:
            if self._state == _OUTSIDE:
                if ch == "{":
                    self._state = _MAYBE
                else:
                    self.stats["noise_chars"] += 1
            elif self._state == _MAYBE:
                if ch.isspace():
                    continue
                if ch in ('"', "}"):
                    self._start_object(ch)
                else:
                    self._state = _OUTSIDE       # prose such as "{like this}" is not a block
                    self.stats["noise_chars"] += 2
                    if ch == "{":
                        self._state = _MAYBE
            else:
                self._inside(ch, out)
        return out

    def finish(self) -> list[Item]:
        out: list[Item] = []
        if self._state == _INSIDE:
            out.append(self._fail(self.ordinal, "truncated", "output ended inside a block", "".join(self._buf)))
            self.ordinal += 1
        self._reset()
        return out

    # ------------------------------------------------------------------ internals
    def _reset(self) -> None:
        self._state, self._buf, self._size, self._depth = _OUTSIDE, [], 0, 0
        self._in_str = self._esc = self._oversize = False

    def _start_object(self, first: str) -> None:
        self._state, self._depth, self._in_str, self._esc, self._oversize = _INSIDE, 1, False, False, False
        self._buf, self._size = ["{"], 1
        if first == "}":
            self._buf.append("}"); self._depth = 0
        else:
            self._buf.append(first); self._in_str = True    # the opening quote of the first key

    def _inside(self, ch: str, out: list[Item]) -> None:
        self._size += 1
        if self._size > self.max_object_chars:
            self._oversize = True
            self._buf = []                                  # stop accumulating; keep tracking depth to resync
        if not self._oversize:
            self._buf.append(ch)
        if self._in_str:
            if self._esc:
                self._esc = False
            elif ch == "\\":
                self._esc = True
            elif ch == '"':
                self._in_str = False
            return
        if ch == '"':
            self._in_str = True
        elif ch == "{":
            self._depth += 1
        elif ch == "}":
            self._depth -= 1
        if self._depth == 0:
            self._finish_object(out)

    def _finish_object(self, out: list[Item]) -> None:
        ordinal, text, oversize = self.ordinal, "".join(self._buf), self._oversize
        self.ordinal += 1
        self._reset()
        if self.ordinal > self.max_blocks:
            if not self._limit_reported:
                self._limit_reported = True
                out.append(self._fail(ordinal, "limit", f"more than {self.max_blocks} blocks; extra blocks ignored", text))
            return
        if oversize:
            out.append(self._fail(ordinal, "limit", f"block larger than {self.max_object_chars} characters", ""))
            return
        try:
            value, repaired = repair_and_load(text)
        except ValueError as e:
            out.append(self._fail(ordinal, "syntax", str(e), text)); return
        if not isinstance(value, dict):
            out.append(self._fail(ordinal, "syntax", "block is not a JSON object", text)); return
        raw = dict(value)
        if isinstance(raw.get("type"), str):
            raw["type"] = raw["type"].strip().lower()
        if not raw.get("id"):
            raw["id"] = make_block_id(self.section, ordinal, raw)
        try:
            block = validate_block(raw)
        except ValidationError as e:
            first = e.errors()[0]
            where = ".".join(str(p) for p in first["loc"][1:] or first["loc"])
            out.append(self._fail(ordinal, "schema", f"{where}: {first['msg']}" if where else first["msg"], text)); return
        self.stats["blocks"] += 1
        self.stats["repaired"] += int(repaired)
        out.append(ParsedBlock(ordinal, block, raw, repaired))

    def _fail(self, ordinal: int, stage: str, message: str, text: str) -> BlockError:
        self.stats["errors"] += 1
        return BlockError(ordinal, stage, message, text[:200])


def parse_all(section: str, text: str) -> list[Item]:
    """Convenience for non-streaming callers and tests."""
    p = BlockStreamParser(section)
    return p.feed(text) + p.finish()
