"""Career skill graph assembly (reads the existing registry, dependency graph, career roles, evidence and study sessions).

No new tables: skills, dependencies, careers, evidence and content sessions already hold everything. This module only
joins them and applies the pure mastery/recommendation engines."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.exceptions import NotFoundError
from app.domains.careers.models import CareerRole
from app.domains.careers.service import CareerService
from app.domains.learning_intelligence.proficiency import Evidence
from app.domains.learning_intelligence.skill_gap import Target
from app.domains.skill_intelligence.mastery import MasteryConfig, SkillMastery, SkillStatus, career_metrics, evaluate, status_of
from app.domains.skill_intelligence.models import STAGES, CareerSkillGraph, GraphEdge, GraphNode, Recommendation, RoleInfo
from app.domains.skill_intelligence.recommend import RecommendConfig, recommend
from app.domains.skill_registry.graph import SkillGraph
from app.domains.skill_registry.models import Skill, SkillStatus as RegistryStatus
from app.domains.skill_registry.normalize import slugify
from app.domains.skill_registry.service import SkillRegistry

STATIC_TTL_S = 60.0


@dataclass
class _Static:
    """Learner-independent structure; cached because it only changes when the registry changes."""
    at: float
    skills: dict[str, Skill]
    graph: SkillGraph
    min_levels: dict[tuple[str, str], float]
    topic_index: dict[str, str]
    scopes: dict[str, tuple[frozenset[str], list[Target]]] = field(default_factory=dict)  # role slug -> (targets + transitive prerequisites, resolved targets)


def _hours_remaining(skill: Skill, proficiency: float, target: float, mastered: bool) -> float:
    if mastered:
        return 0.0
    return round(max(1.0, skill.estimated_hours * max(0.0, target - proficiency) / 5.0), 1)


@dataclass
class Evaluation:
    nodes: dict[str, GraphNode]
    edges: list[GraphEdge]
    targets: list[Target]
    mastery: dict[str, SkillMastery]


class ExplorerService:
    def __init__(self, registry: SkillRegistry, careers: CareerService, store, history, mastery_cfg: MasteryConfig = MasteryConfig(),
                 recommend_cfg: RecommendConfig = RecommendConfig(), ttl_s: float = STATIC_TTL_S):
        self.registry, self.careers, self.store, self.history = registry, careers, store, history
        self.mastery_cfg, self.recommend_cfg, self.ttl_s = mastery_cfg, recommend_cfg, ttl_s
        self._static: _Static | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ cached, learner-independent structure
    def invalidate(self) -> None:
        with self._lock:
            self._static = None

    def static(self) -> _Static:
        st = self._static
        if st is not None and time.monotonic() - st.at < self.ttl_s:
            return st
        with self._lock:
            st = self._static
            if st is not None and time.monotonic() - st.at < self.ttl_s:
                return st
            skills = {s.skill_id: s for s in self.registry.repo.list(limit=100000)[0] if s.status != RegistryStatus.DEPRECATED}
            idx: dict[str, str] = {}
            for s in skills.values():
                for key in (s.name, s.slug, *s.aliases):
                    idx.setdefault(" ".join(key.lower().split()), s.skill_id)
                    idx.setdefault(slugify(key), s.skill_id)
            self._static = _Static(time.monotonic(), skills, self.registry.graph(), self.registry.min_levels(), idx)
            return self._static

    def scope_for(self, st: _Static, role: CareerRole | None) -> tuple[frozenset[str], list[Target]]:
        if role is None:
            return frozenset(st.skills), []
        hit = st.scopes.get(role.slug)
        if hit is None:  # the career -> skill mapping (and its closure over prerequisites) is resolved once per cache lifetime
            targets = []
            for rq in role.requirements:
                sid = st.topic_index.get(slugify(rq.skill_slug))   # same exact slug/alias lookup the registry uses
                if sid:  # registry drift: skip rather than fabricate a skill
                    targets.append(Target(skill_id=sid, target_level=rq.target_level, importance=rq.importance, critical=rq.critical))
            ids = {t.skill_id for t in targets}
            for t in targets:
                ids |= st.graph.transitive_prerequisites(t.skill_id)
            hit = st.scopes.setdefault(role.slug, (frozenset(i for i in ids if i in st.skills), targets))
        return hit

    # ------------------------------------------------------------------ learner-specific evaluation
    def _learning_progress(self, user: str, st: _Static) -> dict[str, float]:
        out: dict[str, float] = {}
        for row in self.history.topic_progress(user) if self.history else []:
            sid = st.topic_index.get(row["normalized_topic"]) or st.topic_index.get(slugify(row["normalized_topic"]))
            if sid and row["total_sections"]:
                out[sid] = max(out.get(sid, 0.0), row["completed_sections"] / row["total_sections"])
        return out

    def evaluate(self, user: str, role: CareerRole | None, now: datetime | None = None) -> Evaluation:
        now = now or datetime.now(timezone.utc)
        st = self.static()
        scope, targets = self.scope_for(st, role)
        tmap = {t.skill_id: t for t in targets}
        by_skill: dict[str, list[Evidence]] = {}
        for e in self.store.evidence_for(user):
            if e.skill_id in scope:
                by_skill.setdefault(e.skill_id, []).append(e)
        progress = self._learning_progress(user, st)
        g = st.graph

        def target_level(sid: str) -> float:
            if sid in tmap:
                return tmap[sid].target_level
            need = [st.min_levels.get((d, sid), self.mastery_cfg.default_target) for d in g.direct_dependents(sid) if d in scope]
            return max(need, default=self.mastery_cfg.default_target)

        mastery: dict[str, SkillMastery] = {sid: evaluate(sid, target_level(sid), by_skill.get(sid, []), now, progress.get(sid, 0.0), self.mastery_cfg)
                                            for sid in scope}
        cur = {sid: m.proficiency for sid, m in mastery.items()}
        # `cur` only has in-scope skills, which is exactly right: a prerequisite outside the scope cannot exist (the scope is closed upward)
        validated = {sid for sid, m in mastery.items() if m.mastered}
        order = g.topological_order(scope)           # prerequisites first; O(V+E)
        depth: dict[str, int] = {}
        miss: dict[str, frozenset[str]] = {}
        for sid in order:
            ps = [p for p in g.direct_prerequisites(sid) if p in scope]
            depth[sid] = max((depth.get(p, 0) + 1 for p in ps), default=0)
            # Prerequisites below the level their dependent needs, plus whatever is missing beneath them. A *validated* (or sufficient)
            # prerequisite stops the walk: its own prerequisites are implied by the mastery shown, so skipped foundations are not
            # reported against skills built on top of it. Memoised per node, so the whole pass is linear in the edges.
            acc: set[str] = set()
            for p in ps:
                if p in validated or cur[p] >= st.min_levels.get((sid, p), self.mastery_cfg.default_target):
                    continue
                acc.add(p); acc |= miss.get(p, frozenset())
            miss[sid] = frozenset(acc)
        missing = {sid: sorted(miss[sid], key=lambda n: (depth[n], st.skills[n].name)) for sid in scope}   # depth order is a valid learning order
        status = {sid: status_of(mastery[sid], bool(missing[sid])) for sid in scope}
        for sid in scope:  # once a skill is validated, prerequisites that were skipped no longer gate it: do not report them as a problem
            if status[sid] == SkillStatus.MASTERED:
                missing[sid] = []
        max_depth = max(depth.values(), default=0)

        nodes: dict[str, GraphNode] = {}
        for sid in sorted(scope):
            s, m, t = st.skills[sid], mastery[sid], tmap.get(sid)
            nodes[sid] = GraphNode(
                skill_id=sid, slug=s.slug, name=s.name, category=s.category, difficulty=s.difficulty, estimated_hours=s.estimated_hours,
                description=s.description, status=status[sid], is_target=t is not None, importance=t.importance if t else 0.0,
                critical=bool(t and t.critical), target=m.target, proficiency=m.proficiency, confidence=m.confidence, evidence_count=m.evidence_count,
                mastery=m.mastery, learning_progress=m.learning_progress, revision_reason=m.revision_reason, missing_prerequisites=missing[sid],
                prerequisites=sorted(p for p in g.direct_prerequisites(sid) if p in scope),
                unlocks=len([d for d in g.direct_dependents(sid) if d in scope]), depth=depth[sid],
                stage=0 if max_depth == 0 else min(len(STAGES) - 1, depth[sid] * len(STAGES) // (max_depth + 1)),
                hours_remaining=_hours_remaining(s, m.proficiency, m.target, m.mastered))
        edges = [GraphEdge(source=p, target=sid, min_proficiency=st.min_levels.get((sid, p), self.mastery_cfg.default_target),
                           satisfied=status[sid] == SkillStatus.MASTERED or cur[p] >= st.min_levels.get((sid, p), self.mastery_cfg.default_target))
                 for sid in sorted(scope) for p in sorted(g.direct_prerequisites(sid)) if p in scope]
        return Evaluation(nodes, edges, targets, mastery)

    # ------------------------------------------------------------------ public read models
    def role(self, slug: str | None) -> CareerRole | None:
        if slug is None:
            return None
        r = self.careers.get_role(slug)
        if not r:
            raise NotFoundError("unknown career role")
        return r

    def career_graph(self, user: str, slug: str | None, now: datetime | None = None) -> CareerSkillGraph:
        """`slug=None` is the career-agnostic view of the whole registry: no targets, hence no readiness and no recommendations."""
        role = self.role(slug)
        ev = self.evaluate(user, role, now)
        nodes = ev.nodes
        metrics = career_metrics(ev.mastery, {sid: n.status for sid, n in nodes.items()}, ev.targets, self.mastery_cfg)
        recs = recommend(nodes, self.static().graph, self.recommend_cfg) if role else []
        return CareerSkillGraph(role=RoleInfo(slug=role.slug, title=role.title, description=role.description) if role else None, nodes=list(nodes.values()), edges=ev.edges,
                                metrics=metrics, categories=sorted({n.category for n in nodes.values()}), recommendations=recs,
                                generated_at=now or datetime.now(timezone.utc))

    def recommendations(self, user: str, slug: str, limit: int = 5) -> list[Recommendation]:
        nodes = self.evaluate(user, self.role(slug)).nodes
        return recommend(nodes, self.static().graph, self.recommend_cfg, limit)

    def resolve(self, text: str) -> Skill:
        st = self.static()
        s = st.skills.get(text)
        if s is None:
            found = self.registry.resolve(text)
            s = st.skills.get(found.skill_id) if found else None
        if s is None:
            raise NotFoundError("skill not found")
        return s

    def related(self, st: _Static, sid: str, scope: frozenset[str], limit: int = 6) -> list[str]:
        """Skills sharing a prerequisite or a dependent with `sid`, then same-category skills; deterministic order."""
        g = st.graph
        sibs: dict[str, int] = {}
        for p in g.direct_prerequisites(sid):
            for o in g.direct_dependents(p):
                sibs[o] = sibs.get(o, 0) + 1
        for d in g.direct_dependents(sid):
            for o in g.direct_prerequisites(d):
                sibs[o] = sibs.get(o, 0) + 1
        cat = st.skills[sid].category
        pool = set(sibs) | {i for i in scope if st.skills[i].category == cat}
        pool -= {sid} | g.direct_prerequisites(sid) | g.direct_dependents(sid)
        ranked = sorted((i for i in pool if i in scope), key=lambda i: (-sibs.get(i, 0), st.skills[i].name))
        return ranked[:limit]

    def skill_detail(self, user: str, skill_ref: str, role_slug: str | None) -> dict:
        skill = self.resolve(skill_ref)
        role = self.role(role_slug)
        nodes = self.evaluate(user, role).nodes
        if skill.skill_id not in nodes:  # not part of this career: evaluate it in the global context instead
            nodes = self.evaluate(user, None).nodes
        st, n = self.static(), nodes[skill.skill_id]
        g = st.graph
        ev = [e for e in self.store.evidence_for(user) if e.skill_id == skill.skill_id]
        sessions = [r for r in self.history.topic_progress(user) if (st.topic_index.get(r["normalized_topic"]) or st.topic_index.get(slugify(r["normalized_topic"]))) == skill.skill_id]

        def brief(i: str) -> dict:
            x = nodes[i]
            return {"skill_id": i, "name": x.name, "status": x.status, "proficiency": x.proficiency, "mastery": x.mastery, "category": x.category}
        prereqs = [{**brief(p), "required_level": st.min_levels.get((skill.skill_id, p), self.mastery_cfg.default_target),
                    "met": nodes[p].proficiency >= st.min_levels.get((skill.skill_id, p), self.mastery_cfg.default_target)}
                   for p in n.prerequisites if p in nodes]
        return {
            "skill": n, "career": {"slug": role.slug, "title": role.title} if role and skill.skill_id in self.scope_for(st, role)[0] else None,
            "prerequisites": prereqs,
            "missing_prerequisites": [brief(m) for m in n.missing_prerequisites if m in nodes],
            "unlocks": [brief(d) for d in sorted(g.direct_dependents(skill.skill_id), key=lambda i: st.skills[i].name) if d in nodes],
            "related": [brief(i) for i in self.related(st, skill.skill_id, frozenset(nodes))],
            "aliases": skill.aliases,
            "evidence": [{"kind": e.kind.value, "score": e.score, "difficulty": e.difficulty, "n_items": e.n_items, "occurred_at": e.occurred_at.isoformat(),
                          "source_id": e.source_id, "graded": not (e.source_id or "").startswith("manual:")}
                         for e in sorted(ev, key=lambda e: e.occurred_at, reverse=True)[:25]],
            "learning_history": [{"session_id": r["id"], "title": r["title"], "status": r["status"], "completed_sections": r["completed_sections"],
                                  "total_sections": r["total_sections"], "updated_at": r["updated_at"].isoformat() if hasattr(r["updated_at"], "isoformat") else str(r["updated_at"])}
                                 for r in sessions[:10]],
        }

    def closure(self, user: str, skill_ref: str, role_slug: str | None, *, upstream: bool, transitive: bool) -> list[GraphNode]:
        skill = self.resolve(skill_ref)
        nodes = self.evaluate(user, self.role(role_slug)).nodes
        if skill.skill_id not in nodes:
            nodes = self.evaluate(user, None).nodes
        g = self.static().graph
        ids = (g.transitive_prerequisites(skill.skill_id) if transitive else g.direct_prerequisites(skill.skill_id)) if upstream else \
              (g.transitive_dependents(skill.skill_id) if transitive else g.direct_dependents(skill.skill_id))
        return sorted((nodes[i] for i in ids if i in nodes), key=lambda n: (n.depth, n.name))

    def search(self, user: str, *, q: str | None, category: str | None, status: SkillStatus | None, role_slug: str | None, offset: int, limit: int) -> tuple[list[GraphNode], int]:
        nodes = self.evaluate(user, self.role(role_slug)).nodes
        needle = (q or "").strip().lower()
        st = self.static()
        rows = [n for n in nodes.values()
                if (not category or n.category == category) and (not status or n.status == status)
                and (not needle or needle in n.name.lower() or needle in n.slug or any(needle in a.lower() for a in st.skills[n.skill_id].aliases))]
        rows.sort(key=lambda n: (n.depth, n.name))
        return rows[offset:offset + limit], len(rows)
