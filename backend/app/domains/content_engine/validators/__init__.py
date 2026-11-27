"""Deterministic validation suite for generated sections. Every check returns structured findings."""
from __future__ import annotations

from typing import Sequence

from pydantic import BaseModel

from app.domains.content_engine.contracts import validate_section
from app.domains.content_engine.findings import Finding, ValidationReport
from app.domains.content_engine.validators.basic import (validate_code, validate_duplicates, validate_ids,
                                                         validate_mermaid_structure, validate_references, validate_structure)
from app.domains.content_engine.validators.latex import KatexRunner, validate_latex

__all__ = ["validate_blocks", "KatexRunner", "Finding", "ValidationReport"]


def _dicts(blocks: Sequence) -> list[dict]:
    return [b.model_dump() if isinstance(b, BaseModel) else dict(b) for b in blocks]


def validate_blocks(section: str, blocks: Sequence, *, katex: KatexRunner | None = None, check_contract: bool = True) -> ValidationReport:
    """Run every deterministic check. Findings are classified critical / error / warning / info.
    ``report.partial`` lists checks that ran in degraded mode (e.g. no Node, so no authoritative LaTeX parse)."""
    bl = _dicts(blocks)
    rep = ValidationReport()
    rep.extend(validate_ids(bl))
    rep.extend(validate_duplicates(bl))
    rep.extend(validate_mermaid_structure(bl))
    rep.extend(validate_code(bl))
    rep.extend(validate_references(bl))
    rep.extend(validate_structure(bl))
    latex = validate_latex(bl, katex)
    rep.extend(latex.findings)
    rep.partial += latex.partial
    if check_contract:
        c = validate_section(section, bl)
        rep.extend(c.findings)
    return rep
