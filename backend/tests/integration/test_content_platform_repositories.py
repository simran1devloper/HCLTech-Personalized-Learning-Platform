"""Content store, jobs/leases/events/ledger and roles: identical behaviour on SQLite and PostgreSQL."""
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.domains.content_engine.contracts import SECTION_IDS
from app.domains.content_engine.models import CourseSpec, normalize_topic
from app.domains.content_engine.versioning import Versions
from app.infrastructure.database.content_store import ContentStore, course_key
from app.infrastructure.database.jobs import JobRepository
from app.infrastructure.database.roles import RoleRepository

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
V = Versions("2.0", "1.0.0", 1, "m", "p")


# ---------------- identity and normalisation ----------------
def test_topic_and_course_identity_is_canonical():
    assert normalize_topic("  Python   Decorators!! ") == "python decorators"
    a, b = CourseSpec("Python Decorators", "intermediate"), CourseSpec("  python  decorators.", "Intermediate", audience=" General ")
    assert course_key(a) == course_key(b) and len(course_key(a)) == 64
    assert course_key(a) != course_key(CourseSpec("Python Decorators", "Beginner")) != course_key(CourseSpec("Python Decorators", "Beginner", language="hi"))


# ---------------- content store ----------------
def test_ensure_course_is_idempotent_and_creates_every_section(db):
    st = ContentStore(db)
    c1 = st.ensure_course(CourseSpec("Python decorators", "Intermediate"), "u1", T0)
    c2 = st.ensure_course(CourseSpec("python  decorators", "intermediate"), "u2", T0)
    assert c1.course_id == c2.course_id and c1.created_by == "u1" and c1.topic == "Python decorators"
    assert [s.section_id for s in st.list_sections(c1.course_id)] == list(SECTION_IDS)
    assert {s.status for s in st.list_sections(c1.course_id)} == {"NOT_STARTED"}
    assert st.get_course(c1.course_id).title.startswith("Python decorators")
    other = st.ensure_course(CourseSpec("Python decorators", "Beginner"), "u1", T0)
    assert other.course_id != c1.course_id and other.topic_id == c1.topic_id          # same topic row, different course


def test_concurrent_course_creation_yields_one_course(db):
    st, ids, errs = ContentStore(db), [], []

    def go():
        try:
            ids.append(st.ensure_course(CourseSpec("Race topic", "Beginner"), "u").course_id)
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errs and len(set(ids)) == 1


def test_save_version_is_atomic_immutable_and_updates_the_section(db):
    st = ContentStore(db)
    c = st.ensure_course(CourseSpec("Caching", "Intermediate"), "u", T0)
    assert st.current_content(c.course_id, "summary") is None
    blocks = [{"id": "blk-001", "type": "heading", "level": 2, "text": "Hi"}, {"id": "blk-002", "type": "text", "markdown": "Body text here."}]
    v1 = st.save_version(c.course_id, "summary", V, blocks=blocks, tokens_in=10, tokens_out=20, now=T0)
    assert v1.content_version == 1 and st.get_section(c.course_id, "summary").status == "COMPLETED"
    cur = st.current_content(c.course_id, "summary")
    assert cur.content_version == 1 and [b["id"] for b in cur.blocks] == ["blk-001", "blk-002"] and cur.tokens_out == 20
    v2 = st.save_version(c.course_id, "summary", Versions("2.0", "1.1.0", 2, "m2", "p"), payload={"legacy": "markdown"}, now=T0 + timedelta(minutes=1))
    assert v2.content_version == 2 and st.current_content(c.course_id, "summary").payload == {"legacy": "markdown"}
    assert [v.content_version for v in st.versions(c.course_id, "summary")] == [1, 2]           # history kept
    assert st.versions(c.course_id, "summary")[0].prompt_version == "1.0.0"
    assert st.get_section(c.course_id, "deep_dive").status == "NOT_STARTED"


def test_duplicate_block_ids_roll_back_the_whole_version(db):
    st = ContentStore(db)
    c = st.ensure_course(CourseSpec("Rollback", "Intermediate"), "u", T0)
    dup = [{"id": "blk-001", "type": "text", "markdown": "a"}, {"id": "blk-001", "type": "text", "markdown": "b"}]
    with pytest.raises(IntegrityError):
        st.save_version(c.course_id, "summary", V, blocks=dup)
    assert st.versions(c.course_id, "summary") == [] and st.get_section(c.course_id, "summary").status == "NOT_STARTED"


def test_completed_content_survives_reopening_the_store(db):
    st = ContentStore(db)
    c = st.ensure_course(CourseSpec("Durable", "Expert"), "u", T0)
    st.save_version(c.course_id, "flashcards", V, payload=[{"question": "q", "answer": "a"}])
    again = ContentStore(db)                                                     # a "restarted process": new objects, same database
    assert again.ensure_course(CourseSpec("durable", "expert")).course_id == c.course_id
    assert again.current_content(c.course_id, "flashcards").payload == [{"question": "q", "answer": "a"}]


# ---------------- jobs: coalescing, claim, lease ----------------
@pytest.fixture
def course(db):
    return ContentStore(db).ensure_course(CourseSpec("Jobs", "Intermediate"), "u", T0)


def test_identical_requests_create_one_job_even_when_racing(db, course):
    jobs, results = JobRepository(db), []

    def go():
        results.append(jobs.create_or_get("key-1", course.course_id, "summary", "u"))
    ts = [threading.Thread(target=go) for _ in range(12)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len({j.job_id for j, _ in results}) == 1 and sum(1 for _, created in results if created) == 1
    j2, created2 = jobs.create_or_get("key-1", course.course_id, "summary", "other")
    assert not created2 and j2.requested_by == "u"                               # the first requester owns the job
    assert jobs.create_or_get("key-2", course.course_id, "summary", "u")[1] is True


def test_exactly_one_worker_claims_a_job(db, course):
    jobs = JobRepository(db)
    job, _ = jobs.create_or_get("k", course.course_id, "summary", "u")
    winners = []

    def go(n):
        if jobs.claim(job.job_id, f"worker-{n}", 30):
            winners.append(n)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(10)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(winners) == 1
    got = jobs.get(job.job_id)
    assert got.status == "GENERATING" and got.lease_owner == f"worker-{winners[0]}" and got.attempts == 1 and got.started_at is not None


def test_lease_heartbeat_expiry_and_takeover(db, course):
    jobs = JobRepository(db)
    job, _ = jobs.create_or_get("k", course.course_id, "summary", "u", now=T0)
    assert jobs.claim(job.job_id, "w1", 30, now=T0)
    assert not jobs.claim(job.job_id, "w2", 30, now=T0 + timedelta(seconds=10))             # lease still valid
    assert jobs.heartbeat(job.job_id, "w1", 30, now=T0 + timedelta(seconds=10))              # extends to T0+40
    assert not jobs.heartbeat(job.job_id, "w2", 30, now=T0 + timedelta(seconds=11))          # not the owner
    assert jobs.expired(T0 + timedelta(seconds=30)) == [] and [j.job_id for j in jobs.expired(T0 + timedelta(seconds=41))] == [job.job_id]
    assert not jobs.heartbeat(job.job_id, "w1", 30, now=T0 + timedelta(seconds=41))          # the dead worker cannot revive its lease
    assert jobs.claim(job.job_id, "w2", 30, now=T0 + timedelta(seconds=41))                  # takeover of an expired lease
    got = jobs.get(job.job_id)
    assert got.lease_owner == "w2" and got.attempts == 2


def test_status_changes_are_owner_checked_and_terminal_states_clear_the_lease(db, course):
    jobs = JobRepository(db)
    job, _ = jobs.create_or_get("k", course.course_id, "summary", "u", now=T0)
    jobs.claim(job.job_id, "w1", 30, now=T0)
    assert not jobs.set_status(job.job_id, "COMPLETED", owner="intruder", now=T0)
    assert jobs.set_status(job.job_id, "VALIDATING", owner="w1", now=T0) and jobs.get(job.job_id).status == "VALIDATING"
    assert jobs.set_status(job.job_id, "COMPLETED", owner="w1", version_id="v1", now=T0)
    done = jobs.get(job.job_id)
    assert done.status == "COMPLETED" and done.lease_owner is None and done.lease_expires_at is None and done.version_id == "v1" and done.completed_at
    assert not jobs.requeue_if_terminal(job.job_id)                                           # completed jobs are not re-run


def test_failed_jobs_can_be_requeued_by_exactly_one_caller(db, course):
    jobs = JobRepository(db)
    job, _ = jobs.create_or_get("k", course.course_id, "summary", "u")
    jobs.claim(job.job_id, "w1")
    jobs.set_status(job.job_id, "FAILED", owner="w1", error="boom")
    assert jobs.get(job.job_id).error == "boom"
    wins = []
    ts = [threading.Thread(target=lambda: wins.append(jobs.requeue_if_terminal(job.job_id))) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert wins.count(True) == 1 and jobs.get(job.job_id).status == "QUEUED" and jobs.get(job.job_id).error is None
    assert jobs.latest_for_section(course.course_id, "summary").job_id == job.job_id and [j.job_id for j in jobs.active_for(course.course_id)] == [job.job_id]


# ---------------- events and ledger ----------------
def test_events_are_monotonic_resumable_and_scoped_to_their_job(db, course):
    jobs = JobRepository(db)
    a, _ = jobs.create_or_get("ka", course.course_id, "summary", "u")
    b, _ = jobs.create_or_get("kb", course.course_id, "deep_dive", "u")
    ids = [jobs.append_event(a.job_id, t, {"n": i}) for i, t in enumerate(["section.started", "block.completed", "block.completed", "section.completed"])]
    jobs.append_event(b.job_id, "section.started")
    assert ids == sorted(ids) and len(set(ids)) == 4
    assert [e.type for e in jobs.events_after(a.job_id)] == ["section.started", "block.completed", "block.completed", "section.completed"]
    resumed = jobs.events_after(a.job_id, after_id=ids[1])                                       # reconnect with Last-Event-ID
    assert [e.event_id for e in resumed] == ids[2:] and resumed[0].data == {"n": 2}
    assert jobs.events_after(a.job_id, after_id=ids[-1]) == [] and len(jobs.events_after(b.job_id)) == 1


def test_usage_ledger_totals_match_what_was_recorded(db, course):
    jobs = JobRepository(db)
    job, _ = jobs.create_or_get("k", course.course_id, "summary", "u")
    for tin, tout in [(100, 50), (200, 70)]:
        jobs.record_usage(job_id=job.job_id, course_id=course.course_id, section_id="summary", provider="p", model="m", tokens_in=tin, tokens_out=tout, latency_ms=1000.0, cost_estimate=0.01)
    jobs.record_usage(job_id=None, course_id=course.course_id, section_id="deep_dive", provider="p", model="m", tokens_in=5, tokens_out=5, latency_ms=1.0, outcome="provider_error")
    assert jobs.usage_totals(course.course_id, "summary") == {"calls": 2, "tokens_in": 300, "tokens_out": 120, "cost_estimate": pytest.approx(0.02)}
    assert jobs.usage_totals(course.course_id)["calls"] == 3 and jobs.usage_totals("nope")["calls"] == 0


# ---------------- roles and audit ----------------
def test_roles_default_to_learner_grant_revoke_and_audit(db):
    from app.infrastructure.database.legacy_client import LegacyDbClient
    LegacyDbClient(db).table("users").insert({"id": "u1", "name": "Ada L", "email": "ada@example.com", "password_hash": "x"}).execute()
    roles = RoleRepository(db)
    assert roles.roles_of("u1") == {"learner"} and roles.roles_of("ghost") == {"learner"}      # existing accounts need no migration
    roles.grant("u1", "reviewer", granted_by="admin-1"); roles.grant("u1", "reviewer")           # idempotent
    roles.grant("u1", "admin")
    assert roles.roles_of("u1") == {"learner", "reviewer", "admin"}
    roles.revoke("u1", "admin")
    assert roles.roles_of("u1") == {"learner", "reviewer"}
    with pytest.raises(ValueError):
        roles.grant("u1", "superuser")
    roles.audit("u1", "gate.approve", "outline", "course-1", {"decision": "ok"}); roles.audit("u2", "x")
    assert [a["action"] for a in roles.audit_entries("u1")] == ["gate.approve"] and roles.audit_entries("u1")[0]["details"] == {"decision": "ok"} and len(roles.audit_entries()) == 2
