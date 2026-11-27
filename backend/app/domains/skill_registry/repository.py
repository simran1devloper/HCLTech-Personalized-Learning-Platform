from __future__ import annotations

from typing import Protocol

from app.domains.skill_registry.models import Dependency, Skill


class SkillRepository(Protocol):
    def get(self, skill_id: str) -> Skill | None: ...
    def get_by_slug(self, slug: str) -> Skill | None: ...
    def list(self, *, category: str | None = None, status: str | None = None,
             q: str | None = None, offset: int = 0, limit: int = 50) -> tuple[list[Skill], int]: ...
    def add(self, skill: Skill) -> Skill: ...
    def update(self, skill: Skill) -> Skill: ...
    def list_dependencies(self) -> list[Dependency]: ...
    def add_dependency(self, dep: Dependency) -> None: ...
    def remove_dependency(self, skill_id: str, prerequisite_id: str) -> None: ...


class InMemorySkillRepository:
    def __init__(self):
        self._skills: dict[str, Skill] = {}
        self._deps: dict[tuple[str, str], Dependency] = {}

    def get(self, skill_id):
        return self._skills.get(skill_id)

    def get_by_slug(self, slug):
        return next((s for s in self._skills.values() if s.slug == slug), None)

    def list(self, *, category=None, status=None, q=None, offset=0, limit=50):
        items = sorted(self._skills.values(), key=lambda s: s.slug)
        if category:
            items = [s for s in items if s.category == category]
        if status:
            items = [s for s in items if s.status.value == status]
        if q:
            ql = q.lower()
            items = [s for s in items if ql in s.name.lower() or any(ql in a.lower() for a in s.aliases)]
        return items[offset: offset + limit], len(items)

    def add(self, skill):
        self._skills[skill.skill_id] = skill
        return skill

    def update(self, skill):
        self._skills[skill.skill_id] = skill
        return skill

    def list_dependencies(self):
        return list(self._deps.values())

    def add_dependency(self, dep):
        self._deps[(dep.skill_id, dep.prerequisite_id)] = dep

    def remove_dependency(self, skill_id, prerequisite_id):
        self._deps.pop((skill_id, prerequisite_id), None)


class SnapshotSkillRepository(InMemorySkillRepository):
    """Reads from a copy loaded once, writes through to ``target``. For bulk work such as seeding, where a query per lookup makes startup take
    minutes against a distant database. Short-lived: it does not see changes made elsewhere."""

    def __init__(self, target: SkillRepository):
        super().__init__()
        self._target = target
        self._skills = {s.skill_id: s for s in target.list(limit=100000)[0]}
        self._deps = {(d.skill_id, d.prerequisite_id): d for d in target.list_dependencies()}

    def add(self, skill):
        self._target.add(skill)
        return super().add(skill)

    def update(self, skill):
        self._target.update(skill)
        return super().update(skill)

    def add_dependency(self, dep):
        if self._deps.get((dep.skill_id, dep.prerequisite_id)) == dep:
            return                                  # already stored: nothing to write
        self._target.add_dependency(dep)
        super().add_dependency(dep)

    def remove_dependency(self, skill_id, prerequisite_id):
        self._target.remove_dependency(skill_id, prerequisite_id)
        super().remove_dependency(skill_id, prerequisite_id)

