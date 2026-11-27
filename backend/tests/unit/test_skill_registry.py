import pytest
from app.core.exceptions import ConflictError, CycleError
from app.domains.skill_registry.graph import SkillGraph
from app.domains.skill_registry.repository import InMemorySkillRepository
from app.domains.skill_registry.seed import seed
from app.domains.skill_registry.service import SkillRegistry
from app.domains.skill_registry.models import SkillStatus


@pytest.fixture
def reg():
    r = SkillRegistry(InMemorySkillRepository())
    seed(r)
    return r


def test_seed_idempotent_and_acyclic(reg):
    n = reg.repo.list(limit=1000)[1]
    seed(reg)
    assert reg.repo.list(limit=1000)[1] == n >= 20
    assert reg.graph().find_cycle() is None


def test_resolution_aliases_and_fuzzy(reg):
    assert reg.resolve("postgres").name == "PostgreSQL"
    assert reg.resolve("Fast API").name == "FastAPI"
    assert reg.resolve("K8s").name == "Kubernetes"
    assert reg.resolve("Pythn").name == "Python"
    assert reg.resolve("underwater basket weaving") is None


def test_no_duplicates(reg):
    with pytest.raises(ConflictError):
        reg.create_skill("python")
    with pytest.raises(ConflictError):
        reg.create_skill("Brand New", aliases=["postgres"])
    assert reg.get_or_propose("PostgreSQL").status == SkillStatus.ACTIVE
    new = reg.get_or_propose("Zig")
    assert new.status == SkillStatus.PROPOSED and reg.get_or_propose("zig").skill_id == new.skill_id


def test_cycle_rejected_and_not_persisted(reg):
    py, fa = reg.resolve("python"), reg.resolve("fastapi")
    before = len(reg.repo.list_dependencies())
    with pytest.raises(CycleError):
        reg.add_dependency(py.skill_id, fa.skill_id)
    with pytest.raises(CycleError):
        reg.add_dependency(py.skill_id, py.skill_id)
    assert len(reg.repo.list_dependencies()) == before


def test_find_cycle_on_corrupt_graph():
    g = SkillGraph([("a", "b"), ("b", "c"), ("c", "a")])
    cyc = g.find_cycle()
    assert cyc and cyc[0] == cyc[-1]
    with pytest.raises(CycleError):
        g.learning_order(["a"])


def test_transitive_and_order(reg):
    g = reg.graph()
    ids = {n: reg.resolve(n, 1.0).skill_id for n in ["Python", "Async Programming", "API Development", "FastAPI", "MCP", "Agentic AI"]}
    pre = g.transitive_prerequisites(ids["FastAPI"])
    assert {ids["Python"], ids["Async Programming"], ids["API Development"]} <= pre
    order = g.learning_order([ids["FastAPI"]])
    pos = {n: i for i, n in enumerate(order)}
    for skill, p in [("Async Programming", "Python"), ("API Development", "Async Programming"), ("FastAPI", "API Development")]:
        assert pos[ids[p]] < pos[ids[skill]]
    assert order[-1] == ids["FastAPI"]
    assert g.learning_order([ids["FastAPI"]]) == order  # deterministic


def test_order_skips_satisfied(reg):
    g = reg.graph()
    py, fa = reg.resolve("python", 1.0).skill_id, reg.resolve("fastapi", 1.0).skill_id
    assert py not in g.learning_order([fa], already_satisfied={py})
    assert py in g.learning_order([fa])


def test_missing_prerequisites_respects_levels(reg):
    g, ml = reg.graph(), reg.min_levels()
    pid = lambda n: reg.resolve(n, 1.0).skill_id
    prof = {pid("Python"): 4.0, pid("API Development"): 1.0}
    missing = g.missing_prerequisites(pid("FastAPI"), prof, ml)
    assert pid("API Development") in missing and pid("Python") not in missing
    assert pid("Async Programming") in missing
    assert missing.index(pid("Async Programming")) < missing.index(pid("API Development"))
    prof2 = {pid("Python"): 4.0, pid("API Development"): 3.0, pid("Async Programming"): 2.5}
    assert g.missing_prerequisites(pid("FastAPI"), prof2, ml) == []
