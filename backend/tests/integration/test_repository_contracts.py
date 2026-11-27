"""Repository contract tests: identical behaviour on SQLite and PostgreSQL (the `db` fixture runs every test on both)."""
import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.core.exceptions import ConflictError, CycleError
from app.domains.learning_intelligence.proficiency import Evidence, EvidenceKind
from app.domains.roadmap_engine.engine import generate
from app.domains.skill_registry.seed import seed
from app.domains.skill_registry.service import SkillRegistry
from app.infrastructure.database.engine import Db
from app.infrastructure.database.learner_store import Attempt, LearnerStore
from app.infrastructure.database.legacy_client import LegacyDbClient
from app.infrastructure.database.migrate import alembic_config, current_revision, ensure_schema
from app.infrastructure.database.skill_repository import SqlSkillRepository
from app.infrastructure.database.tables import metadata

HEAD = max(p.name[:4] for p in (Path(__file__).parents[2] / "alembic" / "versions").glob("0*.py"))  # newest revision id


def ev(sid, src="a1", score=0.8):
    return Evidence(skill_id=sid, kind=EvidenceKind.QUIZ, score=score, difficulty=3, n_items=5, occurred_at=datetime.now(timezone.utc), source_id=src)


@pytest.fixture
def reg(db):
    r = SkillRegistry(SqlSkillRepository(db))
    seed(r)
    return r


# ---------------- skill registry ----------------
def test_skill_registry_behaves_the_same_on_both_dialects(db, reg):
    seed(reg)                                                     # idempotent
    assert reg.repo.list(limit=999)[1] >= 20
    assert reg.resolve("postgres").name == "PostgreSQL" and reg.resolve("Pythn").name == "Python"
    with pytest.raises(ConflictError):
        reg.create_skill("python")
    py, fa = reg.resolve("python", 1.0), reg.resolve("fastapi", 1.0)
    with pytest.raises(CycleError):
        reg.add_dependency(py.skill_id, fa.skill_id)
    assert reg.graph().find_cycle() is None
    items, _ = reg.repo.list(q="k8s")
    assert [s.name for s in items] == ["Kubernetes"]
    items, total = reg.repo.list(category="ai", limit=3, offset=1)
    assert len(items) == 3 and total > 3 and [s.slug for s in items] == sorted(s.slug for s in items)


def test_dependency_upsert_updates_the_required_level(db, reg):
    py, async_ = reg.resolve("python", 1.0), reg.resolve("Async Programming", 1.0)
    reg.repo.add_dependency(type(reg.repo.list_dependencies()[0])(skill_id=async_.skill_id, prerequisite_id=py.skill_id, min_proficiency=4.0))
    got = [d for d in reg.repo.list_dependencies() if d.skill_id == async_.skill_id and d.prerequisite_id == py.skill_id]
    assert len(got) == 1 and got[0].min_proficiency == 4.0


# ---------------- learner store ----------------
def test_evidence_is_idempotent_snapshots_kept_and_learners_isolated(db, reg):
    sid = reg.resolve("python", 1.0).skill_id
    st = LearnerStore(db)
    st.add_evidence("u1", [ev(sid, "att-1")]); st.add_evidence("u1", [ev(sid, "att-1")])
    assert st.estimates("u1")[sid].evidence_count == 1
    st.add_evidence("u1", [ev(sid, "att-2")])
    assert len(st.snapshots("u1", sid)) == 3 and st.estimates("u2") == {} and st.snapshots("u2") == []


def test_evidence_write_is_atomic(db, reg):
    sid = reg.resolve("python", 1.0).skill_id
    st = LearnerStore(db)
    bad = Evidence(skill_id="no-such-skill", kind=EvidenceKind.QUIZ, score=1, difficulty=1, occurred_at=datetime.now(timezone.utc))
    with pytest.raises(Exception):
        st.add_evidence("u1", [ev(sid, "ok"), bad])               # foreign-key violation on the second row
    assert st.evidence_for("u1") == [] and st.snapshots("u1") == []


def test_attempts_roadmaps_goals_and_conversations_are_owner_scoped(db, reg):
    st = LearnerStore(db)
    a = Attempt("att", "u1", "s", ["q1", "q2"], datetime.now(timezone.utc))
    st.save_attempt(a)
    assert st.get_attempt("u1", "att").question_ids == ["q1", "q2"] and st.get_attempt("u2", "att") is None
    a.status, a.results, a.completed_at = "completed", [{"earned": 1}], datetime.now(timezone.utc)
    st.save_attempt(a)
    got = st.get_attempt("u1", "att")
    assert got.status == "completed" and got.completed_at is not None and st.seen_question_ids("u1") == {"q1", "q2"}
    assert [x.attempt_id for x in st.list_attempts("u1")[0]] == ["att"] and st.list_attempts("u2") == ([], 0)

    skills = {s.skill_id: s for s in reg.repo.list(limit=999)[0]}
    from app.domains.careers.service import CareerService
    cs = CareerService(reg)
    rm = generate("u1", "ai-engineer", cs.targets_for(cs.get_role("ai-engineer")), {}, skills, reg.graph())
    st.save_roadmap(rm)
    rm2 = rm.model_copy(update={"version": 2})
    st.save_roadmap(rm2); st.save_roadmap(rm2)                    # same-version upsert
    assert [r.version for r in st.roadmap_versions("u1", rm.roadmap_id)] == [1, 2] and st.roadmap_versions("u2", rm.roadmap_id) == []
    assert [r.version for r in st.list_roadmaps("u1")] == [2]
    st.add_goal("u1", {"goal_id": "g", "role_slug": "ai-engineer", "hours_per_week": 5, "created_at": "2026-01-01T00:00:00+00:00"})
    assert st.list_goals("u1")[0]["role_slug"] == "ai-engineer" and st.list_goals("u2") == []

    st.create_conversation("u1", "c1", "hello", None, "en")
    st.add_message("u1", "c1", "user", "hi"); st.add_message("u1", "c1", "assistant", "hello")
    assert [m["role"] for m in st.messages("u1", "c1")] == ["user", "assistant"] and st.messages("u2", "c1") == []
    with pytest.raises(KeyError):
        st.add_message("u2", "c1", "user", "intruder")


# ---------------- legacy adapter ----------------
def test_legacy_adapter_is_injection_safe_and_enforces_constraints(db):
    c = LegacyDbClient(db)
    c.table("users").insert({"name": "Asha K", "email": "a@e.com", "password_hash": "h"}).execute()
    got = c.table("users").select("id, email").eq("email", "a@e.com").execute().data
    assert got[0]["email"] == "a@e.com" and got[0]["id"]
    assert c.table("users").select("*").eq("email", "x' OR '1'='1").execute().data == []
    for bad in (lambda: c.table("users").select("password_hash; DROP TABLE users").execute(), lambda: c.table("users; DROP TABLE users")):
        with pytest.raises(ValueError):
            bad()
    with pytest.raises(Exception):
        c.table("users").insert({"name": "B B", "email": "a@e.com", "password_hash": "h"}).execute()       # unique email
    with pytest.raises(Exception):
        c.table("learning_history").insert({"user_id": "nope", "topic": "t", "score": 5, "level": "Beginner"}).execute()   # FK
    with db.begin() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM users")).scalar_one() == 1


# ---------------- migrations ----------------
def test_migrations_up_down_up_and_no_drift_between_revisions_and_metadata(db):
    with db.engine.connect() as c:
        ctx = MigrationContext.configure(c, opts={"compare_type": True, "include_object": lambda o, n, t, r, cmp: not (t == "table" and n == "alembic_version")})
        assert compare_metadata(ctx, metadata) == [], "alembic revisions and tables.py have drifted apart"
    assert current_revision(db) == HEAD and ensure_schema(db) == "current"
    cfg = alembic_config(db.url.replace("sqlite+pysqlite", "sqlite"))
    command.downgrade(cfg, "base")
    with db.engine.connect() as c:
        assert sa.inspect(c).get_table_names() == ["alembic_version"]
    command.upgrade(cfg, "head")
    assert current_revision(db) == HEAD


def test_postgres_rejects_dependency_cycles_in_the_database_itself(db, reg):
    if db.dialect != "postgresql":
        pytest.skip("DB-level cycle trigger is PostgreSQL-only (the application guards SQLite)")
    py, fa = reg.resolve("python", 1.0), reg.resolve("fastapi", 1.0)
    with pytest.raises(Exception):
        with db.begin() as c:
            c.execute(sa.text("INSERT INTO skill_dependencies (skill_id, prerequisite_id) VALUES (:a, :b)"), {"a": py.skill_id, "b": fa.skill_id})


# ---------------- sync + async engines over the same metadata ----------------
def test_async_engine_reads_what_the_sync_engine_wrote(db, reg):
    async def read():
        async with db.async_engine.connect() as c:
            return (await c.execute(sa.text("SELECT COUNT(*) FROM skills"))).scalar_one()
    n = asyncio.run(read())
    assert n == reg.repo.list(limit=999)[1] >= 20


# ---------------- adoption of a pre-Alembic SQLite database ----------------
def test_pre_alembic_database_is_stamped_not_recreated_and_keeps_its_data(tmp_path):
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.executescript((Path(__file__).resolve().parents[1] / "fixtures" / "legacy_v2_schema.sql").read_text())
    con.execute("INSERT INTO users VALUES ('u1','Old User','old@example.com','hash','2025-01-01T00:00:00+00:00')")
    con.execute("INSERT INTO learning_history VALUES ('h1','u1','Python',7,'Intermediate','2025-01-02T00:00:00+00:00')")
    con.execute("INSERT INTO tutor_conversations VALUES ('c1','u1','t',NULL,'en','2025-01-03T00:00:00+00:00','2025-01-03T00:00:00+00:00')")
    con.commit(); con.close()
    handle = Db(f"sqlite+pysqlite:///{path}")
    try:
        assert ensure_schema(handle) == "adopted" and current_revision(handle) == HEAD
        with handle.begin() as c:
            assert c.execute(sa.text("SELECT email FROM users")).scalar_one() == "old@example.com"
            assert c.execute(sa.text("SELECT score FROM learning_history")).scalar_one() == 7
            assert c.execute(sa.text("SELECT COUNT(*) FROM courses")).scalar_one() == 0          # new tables exist
        assert LegacyDbClient(handle).table("users").select("name").eq("id", "u1").execute().data == [{"name": "Old User"}]
        assert ensure_schema(handle) == "current"
    finally:
        handle.close()
