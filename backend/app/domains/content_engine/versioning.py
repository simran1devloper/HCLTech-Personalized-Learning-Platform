"""Content versioning and invalidation rules as pure functions (plan §8.6).

Versions are tracked **per section** (and later per atom), so changing one section's prompt never invalidates the others.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True)
class Versions:
    schema_version: str          # block schema, e.g. "2.0"
    prompt_version: str          # semver of the section's prompt, e.g. "1.2.0"
    content_version: int = 1     # increments each time this section is regenerated
    model: str = ""              # provenance only; not a regeneration trigger unless policy says so
    provider: str = ""


@dataclass(frozen=True)
class Decision:
    regenerate: bool
    reasons: tuple[str, ...] = ()


def _parts(v: str) -> tuple[int, int, int]:
    m = _SEMVER.match(v)
    if not m:
        raise ValueError(f"not a semantic version: {v!r}")
    return int(m[1]), int(m[2]), int(m[3])


def is_material_change(old: str, new: str) -> bool:
    """Major or minor changes are material (they change what is asked or produced); patch changes are not."""
    o, n = _parts(old), _parts(new)
    return o[:2] != n[:2]


def schema_compatible(stored: str, current: str) -> bool:
    """Same major schema version means stored content can still be read and rendered."""
    return stored.split(".")[0] == current.split(".")[0]


def decide_regeneration(stored: Versions, current: Versions, *, sources_changed: bool = False, outdated: bool = False,
                        quality_status: str = "approved", user_requested: bool = False,
                        model_sensitive: bool = False) -> Decision:
    reasons: list[str] = []
    if user_requested:
        reasons.append("user_requested")
    if quality_status == "rejected":
        reasons.append("quality_rejected")
    if outdated:
        reasons.append("outdated")
    if sources_changed:
        reasons.append("sources_changed")
    if not schema_compatible(stored.schema_version, current.schema_version):
        reasons.append("schema_incompatible")
    if is_material_change(stored.prompt_version, current.prompt_version):
        reasons.append("prompt_changed_materially")
    if model_sensitive and (stored.model, stored.provider) != (current.model, current.provider):
        reasons.append("model_changed")
    return Decision(bool(reasons), tuple(reasons))


def next_version(stored: Versions, current: Versions) -> Versions:
    """Versions to record after a regeneration: current schema/prompt/model, content version incremented."""
    return replace(current, content_version=stored.content_version + 1)
