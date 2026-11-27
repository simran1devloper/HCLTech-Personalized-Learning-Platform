"""Deterministic next-skill recommendations. Same inputs -> same ranking; no LLM is involved.

score(s) = w_rel*relevance + w_unblock*unblock + w_gap*gap + w_progress*progress + w_uncertainty*uncertainty
           - w_effort*effort + critical_bonus + revision_bonus - blocked_penalty
  relevance   importance of s if it is a career target, else the largest importance among targets that depend on it
  unblock     share of the career's remaining (not yet mastered) importance that flows through s: sum of importance of
              open targets in {s} + dependents(s), divided by the total importance of open targets  ("prerequisite importance")
  gap         (target - validated proficiency) / 5
  progress    study progress already made on s (finish what was started)
  uncertainty 1 - evidence confidence (weak evidence: verify or learn sooner)
  effort      hours remaining / max_effort_hours, capped at 1 (quick wins first)
A skill whose prerequisites are missing is penalised, so its first missing prerequisite ranks above it.
"""
from __future__ import annotations

from pydantic import BaseModel

from app.domains.skill_intelligence.mastery import SkillStatus
from app.domains.skill_intelligence.models import GraphNode, Recommendation
from app.domains.skill_registry.graph import SkillGraph


class RecommendConfig(BaseModel):
    w_relevance: float = 0.25
    w_unblock: float = 0.25
    w_gap: float = 0.20
    w_progress: float = 0.10
    w_uncertainty: float = 0.05
    w_effort: float = 0.15
    critical_bonus: float = 0.05
    revision_bonus: float = 0.05
    blocked_penalty: float = 0.25
    max_effort_hours: float = 60.0
    high: float = 0.40
    medium: float = 0.25


def _explain(n: GraphNode, names: dict[str, str], missing: list[str], unlocks: list[str], action: str) -> str:
    bits = [f"{n.name}: {n.proficiency:.1f}/{n.target:.1f}"]
    if n.is_target:
        bits.append(f"{'critical ' if n.critical else ''}career requirement (importance {n.importance:.0%})")
    else:
        bits.append("prerequisite for your career skills")
    if n.learning_progress > 0:
        bits.append(f"study {n.learning_progress:.0%} done")
    if unlocks:
        bits.append("unlocks " + ", ".join(names[u] for u in unlocks[:3]) + (f" +{len(unlocks) - 3} more" if len(unlocks) > 3 else ""))
    if missing:
        bits.append("learn first: " + ", ".join(names[m] for m in missing[:3]))
    if action == "revise":
        bits.append("evidence is " + ("out of date" if n.revision_reason == "stale" else "showing a regression"))
    if action == "take_assessment":
        bits.append("needs an assessment to confirm your level")
    return "; ".join(bits) + "."


def recommend(nodes: dict[str, GraphNode], graph: SkillGraph, cfg: RecommendConfig = RecommendConfig(), limit: int = 5) -> list[Recommendation]:
    scope = set(nodes)
    open_targets = {i: n for i, n in nodes.items() if n.is_target and n.status != SkillStatus.MASTERED}
    open_total = sum(n.importance for n in open_targets.values()) or 1.0
    names = {i: n.name for i, n in nodes.items()}
    # Downstream reachability as bitsets, built once in reverse topological order: O(E) big-int ORs instead of a graph traversal per skill.
    order = graph.topological_order(scope); bit = {sid: 1 << i for i, sid in enumerate(order)}; desc: dict[str, int] = {}
    for sid in reversed(order):
        b = 0
        for d in graph.direct_dependents(sid):
            if d in scope:
                b |= bit[d] | desc.get(d, 0)
        desc[sid] = b
    target_ids = [i for i, n in nodes.items() if n.is_target]
    scored: list[tuple[float, GraphNode, dict[str, float], list[str]]] = []
    for sid, n in nodes.items():
        if n.status == SkillStatus.MASTERED:
            continue
        down = desc.get(sid, 0)
        relevance = n.importance if n.is_target else max((nodes[t].importance for t in target_ids if down & bit[t]), default=0.0)
        flow = sum(open_targets[t].importance for t in open_targets if t == sid or down & bit[t])
        gap = max(0.0, n.target - n.proficiency) / 5.0
        unlocks = sorted(d for d in graph.direct_dependents(sid) & scope
                         if nodes[d].status != SkillStatus.MASTERED and set(nodes[d].missing_prerequisites) <= {sid} and sid in nodes[d].missing_prerequisites)
        parts = {
            "relevance": cfg.w_relevance * relevance,
            "unblock": cfg.w_unblock * flow / open_total,
            "gap": cfg.w_gap * gap,
            "progress": cfg.w_progress * n.learning_progress,
            "uncertainty": cfg.w_uncertainty * (1 - n.confidence),
            "effort": -cfg.w_effort * min(1.0, n.hours_remaining / cfg.max_effort_hours),
            "critical": cfg.critical_bonus if n.is_target and n.critical else 0.0,
            "revision": cfg.revision_bonus if n.status == SkillStatus.NEEDS_REVISION else 0.0,
            "blocked": -cfg.blocked_penalty if n.missing_prerequisites else 0.0,
        }
        scored.append((round(max(0.0, sum(parts.values())), 4), n, {k: round(v, 4) for k, v in parts.items()}, unlocks))
    scored.sort(key=lambda t: (-t[0], t[1].name))
    out: list[Recommendation] = []
    for rank, (score, n, parts, unlocks) in enumerate(scored[:limit], 1):
        if n.status == SkillStatus.NEEDS_REVISION:
            action = "revise"
        elif n.missing_prerequisites:
            action = "learn_prerequisites"
        elif n.evidence_count == 0:
            action = "start_learning"
        elif n.confidence < 0.5 and n.proficiency >= n.target:
            action = "take_assessment"
        else:
            action = "continue_learning"
        out.append(Recommendation(
            skill_id=n.skill_id, name=n.name, rank=rank, priority="high" if score >= cfg.high else "medium" if score >= cfg.medium else "low",
            score=score, explanation=_explain(n, names, n.missing_prerequisites, unlocks, action), missing_prerequisites=list(n.missing_prerequisites),
            unlocks=unlocks, suggested_action=action, next_skill_id=n.missing_prerequisites[0] if n.missing_prerequisites else None, drivers=parts))
    return out
