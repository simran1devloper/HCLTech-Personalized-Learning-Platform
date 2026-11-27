"""Deterministic roadmap generation and incremental recompilation (no LLM in the loop)."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from app.domains.learning_intelligence.proficiency import ProficiencyEstimate
from app.domains.learning_intelligence.skill_gap import Target
from app.domains.roadmap_engine.models import ItemStatus, Milestone, Roadmap, RoadmapDiff
from app.domains.skill_registry.graph import SkillGraph
from app.domains.skill_registry.models import Skill


@dataclass
class RoadmapConfig:
    mastery_margin: float = 0.0  # skip if current >= target - margin
    min_confidence_to_skip: float = 0.4  # don't skip on weak evidence
    weak_review_threshold: float = 0.75  # completed skill whose level fell below 75% of target is re-introduced
    prereq_satisfied_level: float = 2.0
    pace_hours_per_level: float = 1.0  # multiplier on skill.estimated_hours * gap/5


def _cur(est: dict[str, ProficiencyEstimate], sid: str) -> float:
    return est[sid].proficiency_score if sid in est else 0.0


def _conf(est, sid) -> float:
    return est[sid].confidence_score if sid in est else 0.0


def _hours(skill: Skill, frm: float, to: float, cfg: RoadmapConfig) -> float:
    return round(max(1.0, skill.estimated_hours * max(0.0, to - frm) / 5.0 * cfg.pace_hours_per_level), 1)


def _milestone(skill: Skill, order: int, frm: float, to: float, graph: SkillGraph, resources: dict[str, list[str]],
               cfg: RoadmapConfig, kind="learn", item_id: str | None = None) -> Milestone:
    return Milestone(
        item_id=item_id or str(uuid4()), skill_id=skill.skill_id, skill_name=skill.name, order=order, kind=kind,
        from_level=round(frm, 2), target_level=round(to, 2), prerequisites=sorted(graph.direct_prerequisites(skill.skill_id)),
        estimated_hours=_hours(skill, frm, to, cfg),
        objectives=[f"Reach proficiency {to:.1f}/5 in {skill.name} (currently {frm:.1f})",
                    f"Apply {skill.name} in a hands-on exercise at difficulty {min(5, max(1, round(to)))}"],
        resource_ids=resources.get(skill.skill_id, [])[:5],
        checkpoint=f"Adaptive assessment on {skill.name}",
        completion_criteria={"min_proficiency": round(to, 2), "min_confidence": 0.5})


def _finalize(rm: Roadmap) -> Roadmap:
    active = [m for m in rm.milestones if m.status not in (ItemStatus.SUPERSEDED, ItemStatus.SKIPPED_MASTERED, ItemStatus.COMPLETED)]
    rm.total_hours = round(sum(m.estimated_hours for m in active), 1)
    rm.estimated_weeks = round(rm.total_hours / rm.hours_per_week, 1)
    return rm


def _evaluate_blocking(rm: Roadmap, est: dict[str, ProficiencyEstimate], cfg: RoadmapConfig) -> None:
    """A pending milestone is blocked when a prerequisite is neither adequately proficient nor scheduled earlier."""
    done_or_ok = {m.skill_id for m in rm.milestones if m.status in (ItemStatus.COMPLETED, ItemStatus.SKIPPED_MASTERED)}
    scheduled_before: dict[str, int] = {m.skill_id: m.order for m in rm.milestones
                                        if m.status not in (ItemStatus.SUPERSEDED, ItemStatus.SKIPPED_MASTERED)}
    for m in rm.milestones:
        if m.status not in (ItemStatus.PENDING, ItemStatus.BLOCKED, ItemStatus.IN_PROGRESS):
            continue
        blockers = []
        for p in m.prerequisites:
            ok = p in done_or_ok or _cur(est, p) >= cfg.prereq_satisfied_level
            if not ok and not (p in scheduled_before and scheduled_before[p] < m.order):
                blockers.append(p)  # prerequisite is neither satisfied nor planned earlier: unresolvable as ordered
        m.blocked_by = blockers
        if blockers:
            m.status = ItemStatus.BLOCKED
        elif m.status == ItemStatus.BLOCKED:
            m.status = ItemStatus.PENDING


def generate(learner_id: str, goal: str, targets: list[Target], est: dict[str, ProficiencyEstimate],
             skills: dict[str, Skill], graph: SkillGraph, *, hours_per_week: float = 5.0,
             resources: dict[str, list[str]] | None = None, cfg: RoadmapConfig = RoadmapConfig()) -> Roadmap:
    resources = resources or {}
    tgt = {t.skill_id: t.target_level for t in targets}
    satisfied = {s for s, lvl in tgt.items() if _cur(est, s) >= lvl - cfg.mastery_margin and _conf(est, s) >= cfg.min_confidence_to_skip}
    # prerequisites that are already sufficiently strong are satisfied too
    for sid in graph.nodes():
        if _cur(est, sid) >= cfg.prereq_satisfied_level + 1 and sid not in tgt:
            satisfied.add(sid)
    order = graph.learning_order(list(tgt), already_satisfied=satisfied)
    ms: list[Milestone] = []
    for sid in order:
        sk = skills[sid]
        to = tgt.get(sid, cfg.prereq_satisfied_level + 0.5)  # pure prerequisite: bring to working level
        frm = _cur(est, sid)
        if frm >= to and _conf(est, sid) >= cfg.min_confidence_to_skip:
            continue  # demonstrably mastered; weak evidence is not enough to skip
        ms.append(_milestone(sk, len(ms) + 1, frm, to, graph, resources, cfg))
    rm = Roadmap(roadmap_id=str(uuid4()), learner_id=learner_id, goal=goal, hours_per_week=hours_per_week, milestones=ms,
                 change_summary=["initial roadmap"])
    _evaluate_blocking(rm, est, cfg)
    return _finalize(rm)


def recompile(prev: Roadmap, targets: list[Target], est: dict[str, ProficiencyEstimate], skills: dict[str, Skill],
              graph: SkillGraph, *, resources: dict[str, list[str]] | None = None,
              cfg: RoadmapConfig = RoadmapConfig()) -> tuple[Roadmap, RoadmapDiff]:
    """Incrementally update `prev` after new evidence. Preserves completed items and the ids of unchanged
    pending items; returns the same version when nothing meaningful changed."""
    resources = resources or {}
    diff = RoadmapDiff()
    tgt = {t.skill_id: t.target_level for t in targets}
    old = [m.model_copy(deep=True) for m in prev.milestones]
    prev_status = {m.item_id: m.status for m in old}
    out: list[Milestone] = []

    for m in old:
        if m.status == ItemStatus.SUPERSEDED:
            out.append(m)
            continue
        cur = _cur(est, m.skill_id)
        if m.status == ItemStatus.COMPLETED:
            diff.preserved_completed += 1
            out.append(m)
            continue
        if m.status == ItemStatus.SKIPPED_MASTERED:
            out.append(m)
            continue
        if m.skill_id not in tgt and m.kind == "learn" and m.skill_id not in {p for x in old for p in x.prerequisites}:
            m.status = ItemStatus.SUPERSEDED
            diff.removed.append(m.skill_name)
        elif cur >= m.target_level and _conf(est, m.skill_id) >= cfg.min_confidence_to_skip and m.status != ItemStatus.IN_PROGRESS:
            m.status = ItemStatus.SKIPPED_MASTERED
            diff.skipped.append(m.skill_name)
        else:
            m.from_level = round(cur, 2)
            m.estimated_hours = _hours(skills[m.skill_id], cur, m.target_level, cfg)
        out.append(m)

    # reintroduce weak completed/skipped skills
    for m in list(out):
        if m.status in (ItemStatus.COMPLETED, ItemStatus.SKIPPED_MASTERED) and m.skill_id in tgt:
            cur = _cur(est, m.skill_id)
            if sum(1 for x in out if x.skill_id == m.skill_id and x.status not in (ItemStatus.SUPERSEDED, ItemStatus.COMPLETED, ItemStatus.SKIPPED_MASTERED)):
                continue
            if m.skill_id in est and cur < tgt[m.skill_id] * cfg.weak_review_threshold:
                out.append(_milestone(skills[m.skill_id], 0, cur, tgt[m.skill_id], graph, resources, cfg, kind="review"))
                diff.reintroduced.append(m.skill_name)

    # add newly required skills
    live_skills = {m.skill_id for m in out if m.status not in (ItemStatus.SUPERSEDED,)}
    satisfied = {s for s in live_skills if any(m.skill_id == s and m.status in (ItemStatus.COMPLETED, ItemStatus.SKIPPED_MASTERED) for m in out)}
    new_order = graph.learning_order([s for s in tgt if s not in live_skills], already_satisfied=satisfied | {s for s in live_skills})
    for sid in new_order:
        if sid in live_skills or _cur(est, sid) >= tgt.get(sid, cfg.prereq_satisfied_level + 0.5):
            continue
        out.append(_milestone(skills[sid], 0, _cur(est, sid), tgt.get(sid, cfg.prereq_satisfied_level + 0.5), graph, resources, cfg))
        diff.added.append(skills[sid].name)

    # stable re-ordering: topological position over live pending items; completed keep relative first position
    live = [m for m in out if m.status not in (ItemStatus.SUPERSEDED,)]
    pos = {sid: i for i, sid in enumerate(graph.learning_order([m.skill_id for m in live]))}
    live.sort(key=lambda m: (m.status not in (ItemStatus.COMPLETED, ItemStatus.SKIPPED_MASTERED), pos.get(m.skill_id, 10**6), m.item_id))
    for i, m in enumerate(live, 1):
        m.order = i
    dead = [m for m in out if m.status == ItemStatus.SUPERSEDED]

    new = prev.model_copy(deep=True)
    new.milestones = live + dead
    _evaluate_blocking(new, est, cfg)
    for m in new.milestones:
        before, after = prev_status.get(m.item_id), m.status
        if before and before != ItemStatus.BLOCKED and after == ItemStatus.BLOCKED:
            diff.blocked.append(m.skill_name)
        if before == ItemStatus.BLOCKED and after == ItemStatus.PENDING:
            diff.unblocked.append(m.skill_name)
    if not diff.changed:
        # nothing meaningful changed -> no new version, but keep refreshed hour estimates out of history churn
        return prev, diff
    new.version = prev.version + 1
    new.change_summary = ([f"added: {', '.join(diff.added)}"] if diff.added else []) + \
        ([f"skipped (mastered): {', '.join(diff.skipped)}"] if diff.skipped else []) + \
        ([f"reintroduced for review: {', '.join(diff.reintroduced)}"] if diff.reintroduced else []) + \
        ([f"removed: {', '.join(diff.removed)}"] if diff.removed else []) + \
        ([f"now blocked: {', '.join(diff.blocked)}"] if diff.blocked else []) + \
        ([f"unblocked: {', '.join(diff.unblocked)}"] if diff.unblocked else [])
    return _finalize(new), diff


def complete_milestone(rm: Roadmap, item_id: str, est: dict[str, ProficiencyEstimate]) -> Milestone:
    """Completion is evidence-gated: criteria must be met by the proficiency engine, not by a client claim."""
    from datetime import datetime, timezone
    from app.core.exceptions import ConflictError, NotFoundError
    m = next((x for x in rm.milestones if x.item_id == item_id), None)
    if not m:
        raise NotFoundError("milestone not found")
    if m.status == ItemStatus.BLOCKED:
        raise ConflictError("milestone is blocked by unmet prerequisites", details={"blocked_by": m.blocked_by})
    crit = m.completion_criteria
    if _cur(est, m.skill_id) < crit["min_proficiency"] or _conf(est, m.skill_id) < crit["min_confidence"]:
        raise ConflictError("completion criteria not met; take the checkpoint assessment", details=crit)
    m.status, m.completed_at = ItemStatus.COMPLETED, datetime.now(timezone.utc)
    _finalize(rm)
    return m
