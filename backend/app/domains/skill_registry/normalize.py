"""Skill-name normalisation and fuzzy matching (deterministic, no LLM)."""
from __future__ import annotations

import difflib
import re
import unicodedata

_SPECIAL = {"c++": "cpp", "c#": "csharp", ".net": "dotnet", "node.js": "nodejs", "next.js": "nextjs"}


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKC", name).strip().lower()
    for k, v in _SPECIAL.items():
        s = s.replace(k, v)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def fuzzy_best(query: str, candidates: dict[str, str], cutoff: float = 0.88) -> tuple[str, float] | None:
    """candidates: slug -> skill_id. Returns (skill_id, ratio) of the closest slug, or None."""
    q = slugify(query)
    if not q:
        return None
    if q in candidates:
        return candidates[q], 1.0
    best = difflib.get_close_matches(q, candidates.keys(), n=1, cutoff=cutoff)
    if not best:
        return None
    return candidates[best[0]], difflib.SequenceMatcher(None, q, best[0]).ratio()
