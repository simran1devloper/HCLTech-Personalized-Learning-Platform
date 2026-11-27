from __future__ import annotations

from app.core.exceptions import ConflictError, NotFoundError
from app.domains.skill_registry.graph import SkillGraph
from app.domains.skill_registry.models import Dependency, Skill, SkillStatus, utcnow
from app.domains.skill_registry.normalize import fuzzy_best, slugify
from app.domains.skill_registry.repository import SkillRepository


class SkillRegistry:
    def __init__(self, repo: SkillRepository):
        self.repo = repo

    # ---- skills -------------------------------------------------------
    def _lookup_index(self) -> dict[str, str]:
        idx: dict[str, str] = {}
        skills, _ = self.repo.list(limit=100000)
        for s in skills:
            idx[s.slug] = s.skill_id
            for a in s.aliases:
                idx.setdefault(slugify(a), s.skill_id)
        return idx

    def resolve(self, text: str, fuzzy_cutoff: float = 0.88) -> Skill | None:
        """Map free text ('postgres', 'Fast API') onto a canonical skill, or None."""
        hit = fuzzy_best(text, self._lookup_index(), fuzzy_cutoff)
        return self.repo.get(hit[0]) if hit else None

    def create_skill(self, name: str, **fields) -> Skill:
        slug = slugify(name)
        if not slug:
            raise ConflictError("skill name is empty after normalisation")
        existing = self.resolve(name, fuzzy_cutoff=1.0)  # exact slug/alias only: no fuzzy auto-merge on create
        if existing:
            raise ConflictError(f"skill already exists: {existing.name}", details={"skill_id": existing.skill_id})
        aliases = list(dict.fromkeys(fields.pop("aliases", [])))
        idx = self._lookup_index()
        clash = [a for a in aliases if slugify(a) in idx]
        if clash:
            raise ConflictError("alias collides with an existing skill", details={"aliases": clash})
        if (pid := fields.get("parent_skill_id")) and not self.repo.get(pid):
            raise NotFoundError("parent skill not found")
        return self.repo.add(Skill(slug=slug, name=name.strip(), aliases=aliases, **fields))

    def get_or_propose(self, name: str) -> Skill:
        """Discovery path: reuse a matching skill or register a PROPOSED one (never duplicates)."""
        found = self.resolve(name)
        if found:
            return found
        return self.create_skill(name, status=SkillStatus.PROPOSED)

    def deprecate(self, skill_id: str) -> Skill:
        s = self.repo.get(skill_id)
        if not s:
            raise NotFoundError("skill not found")
        return self.repo.update(s.model_copy(update={"status": SkillStatus.DEPRECATED, "updated_at": utcnow()}))

    # ---- graph --------------------------------------------------------
    def graph(self) -> SkillGraph:
        return SkillGraph((d.skill_id, d.prerequisite_id) for d in self.repo.list_dependencies())

    def add_dependency(self, skill_id: str, prerequisite_id: str, min_proficiency: float = 2.0) -> Dependency:
        for sid in (skill_id, prerequisite_id):
            if not self.repo.get(sid):
                raise NotFoundError("skill not found", details={"skill_id": sid})
        self.graph().add_edge(skill_id, prerequisite_id)  # raises CycleError; nothing persisted on failure
        dep = Dependency(skill_id=skill_id, prerequisite_id=prerequisite_id, min_proficiency=min_proficiency)
        self.repo.add_dependency(dep)
        return dep

    def min_levels(self) -> dict[tuple[str, str], float]:
        return {(d.skill_id, d.prerequisite_id): d.min_proficiency for d in self.repo.list_dependencies()}
