"""Turns raw model tokens into UI-safe chunks while a section is being written.

* ``MarkdownChunker`` releases text at line/word boundaries and never while a code fence (incl. ```mermaid) is open, so the
  client can append deltas and re-render without ever showing a half-open diagram.
* ``ArrayItemScanner`` finds each complete object of the first JSON array in a growing buffer (key concepts, flashcards)
  so list items can appear one by one.

Everything streamed is *provisional*: the validated ``section`` event that follows always replaces it.
"""
from __future__ import annotations

import json
from typing import Any, List, Optional


class MarkdownChunker:
    def __init__(self, soft_limit: int = 200):
        self._buf = ""
        self._soft = soft_limit  # a paragraph this long is released at the last space instead of waiting for its newline

    @staticmethod
    def _fence_open(text: str) -> bool:
        return text.count("```") % 2 == 1

    def feed(self, token: str) -> Optional[str]:
        """Add a token; return a delta that is safe to show, or None."""
        self._buf += token
        cut = self._buf.rfind("\n")
        while cut != -1 and self._fence_open(self._buf[: cut + 1]):
            cut = self._buf.rfind("\n", 0, cut)          # an earlier newline that is outside any open fence
        if cut == -1 and len(self._buf) >= self._soft and not self._fence_open(self._buf):
            cut = self._buf.rfind(" ")
        if cut == -1:
            return None
        out, self._buf = self._buf[: cut + 1], self._buf[cut + 1:]
        return out or None

    def flush(self) -> Optional[str]:
        out, self._buf = self._buf, ""
        return out or None


class ArrayItemScanner:
    """Incrementally extracts the objects of the first JSON array: ``{"k": [{..}, {..}]}`` or a bare ``[{..}, {..}]``."""

    def __init__(self):
        self._depth = 0
        self._arr: Optional[int] = None
        self._in_str = self._esc = self._collecting = self._closed = False
        self._buf: List[str] = []

    def feed(self, text: str) -> List[dict]:
        out: List[dict] = []
        for ch in text:
            if self._closed:
                break
            if self._in_str:
                self._take(ch)
                if self._esc:
                    self._esc = False
                elif ch == "\\":
                    self._esc = True
                elif ch == '"':
                    self._in_str = False
                continue
            if ch == '"':
                self._in_str = True
                self._take(ch)
            elif ch == "[":
                self._depth += 1
                if self._arr is None:
                    self._arr = self._depth
                else:
                    self._take(ch)
            elif ch == "{":
                self._depth += 1
                if self._arr is not None and self._depth == self._arr + 1:
                    self._collecting, self._buf = True, []
                self._take(ch)
            elif ch in "}]":
                self._take(ch)
                self._depth -= 1
                if ch == "}" and self._collecting and self._depth == self._arr:
                    self._collecting = False
                    try:
                        obj: Any = json.loads("".join(self._buf))
                    except ValueError:
                        continue
                    if isinstance(obj, dict):
                        out.append(obj)
                elif ch == "]" and self._arr is not None and self._depth < self._arr:
                    self._closed = True
            else:
                self._take(ch)
        return out

    def _take(self, ch: str) -> None:
        if self._collecting:
            self._buf.append(ch)
