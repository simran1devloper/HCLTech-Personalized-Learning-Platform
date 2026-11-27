"""Prerequisite DAG: validation, cycle detection, traversal, ordering. Pure functions, no I/O."""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable

from app.core.exceptions import CycleError


class SkillGraph:
    """Edge direction: prerequisite -> dependent (learn tail before head)."""

    def __init__(self, edges: Iterable[tuple[str, str]] = ()):
        # edges are (skill_id, prerequisite_id)
        self._prereqs: dict[str, set[str]] = defaultdict(set)
        self._dependents: dict[str, set[str]] = defaultdict(set)
        for skill, pre in edges:
            self._prereqs[skill].add(pre)
            self._dependents[pre].add(skill)

    def nodes(self) -> set[str]:
        return set(self._prereqs) | set(self._dependents)

    def direct_prerequisites(self, s: str) -> set[str]:
        return set(self._prereqs.get(s, ()))

    def direct_dependents(self, s: str) -> set[str]:
        return set(self._dependents.get(s, ()))

    def would_create_cycle(self, skill: str, prerequisite: str) -> bool:
        """Adding skill->requires->prerequisite creates a cycle iff skill is already reachable
        from `prerequisite` via prerequisites (i.e. prerequisite transitively requires skill)."""
        if skill == prerequisite:
            return True
        return skill in self.transitive_prerequisites(prerequisite)

    def add_edge(self, skill: str, prerequisite: str) -> None:
        if self.would_create_cycle(skill, prerequisite):
            raise CycleError("dependency would create a cycle",
                             details={"skill": skill, "prerequisite": prerequisite})
        self._prereqs[skill].add(prerequisite)
        self._dependents[prerequisite].add(skill)

    def remove_edge(self, skill: str, prerequisite: str) -> None:
        self._prereqs[skill].discard(prerequisite)
        self._dependents[prerequisite].discard(skill)

    def transitive_prerequisites(self, s: str) -> set[str]:
        seen: set[str] = set()
        stack = list(self._prereqs.get(s, ()))
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(self._prereqs.get(n, ()))
        return seen

    def transitive_dependents(self, s: str) -> set[str]:
        seen: set[str] = set()
        stack = list(self._dependents.get(s, ()))
        while stack:
            n = stack.pop()
            if n not in seen:
                seen.add(n)
                stack.extend(self._dependents.get(n, ()))
        return seen

    def topological_order(self, ids: Iterable[str]) -> list[str]:
        """Prerequisites before dependents, restricted to `ids` (edges leaving the subset are ignored). Iterative (safe for very deep chains)
        and deterministic (ties by id). Nodes on a corrupt cycle are appended last instead of raising, so read models never fail on bad data."""
        subset = set(ids)
        indeg = {n: len(self._prereqs.get(n, set()) & subset) for n in subset}
        ready = deque(sorted(n for n, d in indeg.items() if d == 0))
        order: list[str] = []
        while ready:
            n = ready.popleft()
            order.append(n)
            newly = []
            for m in self._dependents.get(n, ()):
                if m in subset:
                    indeg[m] -= 1
                    if indeg[m] == 0:
                        newly.append(m)
            ready.extend(sorted(newly))
        order.extend(sorted(subset - set(order)))
        return order

    def find_cycle(self) -> list[str] | None:
        """Validate an existing graph (e.g. loaded from DB). Returns one cycle or None."""
        WHITE, GREY, BLACK = 0, 1, 2
        color: dict[str, int] = defaultdict(int)
        path: list[str] = []

        def visit(n: str) -> list[str] | None:
            color[n] = GREY
            path.append(n)
            for m in sorted(self._prereqs.get(n, ())):
                if color[m] == GREY:
                    return path[path.index(m):] + [m]
                if color[m] == WHITE and (c := visit(m)):
                    return c
            path.pop()
            color[n] = BLACK
            return None

        for n in sorted(self.nodes()):
            if color[n] == WHITE and (c := visit(n)):
                return c
        return None

    def learning_order(self, targets: Iterable[str], already_satisfied: set[str] = frozenset()) -> list[str]:
        """Topological order of targets + all unsatisfied transitive prerequisites.
        Deterministic: ties broken by id. Satisfied skills (and their exclusive ancestors) are skipped."""
        needed: set[str] = set()
        stack = [t for t in targets if t not in already_satisfied]
        while stack:
            n = stack.pop()
            if n in needed or n in already_satisfied:
                continue
            needed.add(n)
            stack.extend(self._prereqs.get(n, ()))
        indeg = {n: len(self._prereqs.get(n, set()) & needed) for n in needed}
        ready = deque(sorted(n for n, d in indeg.items() if d == 0))
        order: list[str] = []
        while ready:
            n = ready.popleft()
            order.append(n)
            newly = []
            for m in self._dependents.get(n, ()):
                if m in needed:
                    indeg[m] -= 1
                    if indeg[m] == 0:
                        newly.append(m)
            ready.extend(sorted(newly))
        if len(order) != len(needed):
            raise CycleError("graph contains a cycle", details={"cycle": self.find_cycle() or []})
        return order

    def missing_prerequisites(self, skill: str, proficiency: dict[str, float],
                              min_levels: dict[tuple[str, str], float] | None = None,
                              default_min: float = 2.0) -> list[str]:
        """Transitive prerequisites whose current proficiency is below the required level,
        returned in learning order."""
        min_levels = min_levels or {}
        pre = self.transitive_prerequisites(skill)
        scope = pre | {skill}
        missing = set()
        for p in pre:
            # required level = strictest requirement placed on p by anything in this skill's subtree
            need = max([min_levels.get((d, p), default_min) for d in self._dependents.get(p, ()) if d in scope]
                       or [default_min])
            if proficiency.get(p, 0.0) < need:
                missing.add(p)
        return [n for n in self.learning_order(missing) if n in missing]
