"""Extract + validate model JSON against a Pydantic schema before it reaches domain code."""
from __future__ import annotations

import json
import re
from typing import Any, Type

from pydantic import ValidationError

from app.ai.types import M
from app.core.exceptions import StructuredOutputError


def extract_json(text: str) -> Any:
    """Parse JSON from raw model text (fences / surrounding prose tolerated)."""
    if not text or not text.strip():
        raise StructuredOutputError("empty model output")
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start = cleaned.find(open_c)
        if start == -1:
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if esc:
                esc = False
            elif ch == "\\" and in_str:
                esc = True
            elif ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == open_c:
                    depth += 1
                elif ch == close_c:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cleaned[start : i + 1])
                        except json.JSONDecodeError:
                            break
    raise StructuredOutputError("no valid JSON found in model output")


def parse_structured(text: str, schema: Type[M]) -> M:
    data = extract_json(text)
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        # Do not echo model output into the error (may contain injected content).
        raise StructuredOutputError("model output failed schema validation",
                                    details={"errors": [x["loc"] for x in e.errors()][:10]}) from e
