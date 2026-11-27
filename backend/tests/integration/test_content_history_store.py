"""Content-history persistence: ownership, versions, jobs/leases, recovery, search. Runs on SQLite and (when reachable) PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app.infrastructure.database import tables as T
from app.infrastructure.database.content_history_store import ContentHistoryStore

SECS = [("summary", "Summary"), ("key_concepts", "Key concepts"), ("deep_dive", "Deep dive")]


@pytest.fixture
def store(db):
    return ContentHistoryStore(db)


def mk(store, user="alice", topic="Machine Learning", level="Intermediate", **kw):
    return store.create_session(user, topic, level, SECS, **kw)


def test_create_persists_session_and_all_sections_across_a_new_store_handle(db):
    s = mk(ContentHistoryStore(db))
    again = ContentHistoryStore(db)  # a "restarted" API process sees the same rows
    got = again.get_session("alice", s["id"])
    assert got["title"] == "Machine Learning" and got["total_sections"] == 3 and got["completed_sections"] == 0 and got["status"] == "active"
    assert [x["status"] for x in again.list_sections("alice", s["id"], ["summary", "key_concepts", "deep_dive"])] == ["NOT_STARTED"] * 3


def test_save_section_is_independent_versioned_and_idempotent(store):
    s = mk(store)
    m1 = store.save_section("alice", s["id"], "summary", "## v1", prompt_version="p1", provider="ollama", model="m", tokens_in=10, tokens_out=20)
    assert m1["status"] == "COMPLETED" and m1["content_version"] == 1
    assert store.get_session("alice", s["id"])["completed_sections"] == 1
    assert store.save_section("alice", s["id"], "summary", "## v1")["content_version"] == 1           # same content: no new version
    assert store.save_section("alice", s["id"], "summary", "## v2", force_new=True)["content_version"] == 2
    vs = store.versions("alice", s["id"], "summary", include_content=True)
    assert [(v["version"], v["content"], v["active"]) for v in vs] == [(2, "## v2", True), (1, "## v1", False)]
    assert store.get_section("alice", s["id"], "summary")["content"] == "## v2"
    assert store.get_section("alice", s["id"], "deep_dive")["status"] == "NOT_STARTED"                 # untouched
    store.activate_version("alice", s["id"], "summary", 1)
    assert store.get_section("alice", s["id"], "summary")["content"] == "## v1"
    assert len(store.versions("alice", s["id"], "summary")) == 2                                      # nothing deleted


def test_session_completes_when_every_section_is_saved(store):
    s = mk(store)
    for k, _ in SECS:
        store.save_section("alice", s["id"], k, f"content {k}")
    got = store.get_session("alice", s["id"])
    assert got["status"] == "completed" and got["completed_sections"] == 3 == got["total_sections"]
    assert set(got["context"]["generated_sections"]) == {"summary", "key_concepts", "deep_dive"}


def test_cross_user_isolation_on_every_operation(store):
    s = mk(store)
    sid, bob = s["id"], "bob"
    store.save_section("alice", sid, "summary", "secret")
    assert store.get_session(bob, sid) is None
    assert store.list_sessions(bob)[0] == [] and store.list_sessions(bob, q="machine")[1] == 0
    assert store.list_sections(bob, sid) is None and store.get_section(bob, sid, "summary") is None
    assert store.get_contents(bob, sid) == {} and store.versions(bob, sid, "summary") is None
    assert store.save_section(bob, sid, "summary", "pwn") is None
    assert store.update_session(bob, sid, title="mine") is None
    assert store.archive(bob, sid) is False and store.restore(bob, sid) is False and store.delete(bob, sid) is False
    assert store.duplicate(bob, sid) is None and store.activate_version(bob, sid, "summary", 1) is None
    assert store.jobs_for_session(bob, sid) is None and store.session_events(bob, sid) is None
    assert store.add_feedback(bob, sid, section_key=None, rating=5, comment="x") is False
    assert store.get_section("alice", sid, "summary")["content"] == "secret" and store.get_session("alice", sid)["title"] == "Machine Learning"


def test_archive_restore_soft_delete_and_audit(db, store):
    s = mk(store)
    assert store.archive("alice", s["id"])
    assert store.list_sessions("alice")[1] == 0 and store.list_sessions("alice", status="archived")[1] == 1
    assert store.get_session("alice", s["id"])["status"] == "archived"
    assert store.restore("alice", s["id"]) and store.get_session("alice", s["id"])["status"] == "active"
    assert store.delete("alice", s["id"])
    assert store.get_session("alice", s["id"]) is None and store.list_sessions("alice", status="archived")[1] == 0
    assert store.delete("alice", s["id"]) is False
    with db.begin() as c:
        actions = [r[0] for r in c.execute(T.audit_log.select().with_only_columns(T.audit_log.c.action).where(T.audit_log.c.entity_id == s["id"]))]
    assert actions == ["content_session.archive", "content_session.restore", "content_session.delete"]
    assert store.purge_deleted(timedelta(0), now=T.utcnow() + timedelta(seconds=1)) == 1


def test_search_filter_sort_pagination_and_tags(store):
    a = mk(store, topic="Machine Learning Fundamentals")
    b = mk(store, topic="REST API Design", level="Advanced", content_type="technical_deep_dive")
    mk(store, topic="System Design Interview", level="Advanced", content_type="interview_preparation")
    store.save_section("alice", a["id"], "key_concepts", [{"term": "Gradient descent", "definition": "x"}])
    store.update_session("alice", b["id"], tags=["Backend", "http"])
    names = lambda **kw: sorted(i["title"] for i in store.list_sessions("alice", **kw)[0])  # noqa: E731
    assert names(q="gradient") == ["Machine Learning Fundamentals"]            # associated concept
    assert names(q="backend") == ["REST API Design"]                           # tag
    assert names(q="interview") == ["System Design Interview"]                 # content type / title
    assert names(q="design advanced") == ["REST API Design", "System Design Interview"] or names(q="design") == ["REST API Design", "System Design Interview"]
    assert names(q="100%") == [] and names(q="_") == []                        # LIKE wildcards are escaped
    assert names(content_type="technical_deep_dive") == ["REST API Design"]
    items, total = store.list_sessions("alice", limit=2, offset=0, sort="alphabetical")
    assert total == 3 and [i["title"] for i in items] == ["Machine Learning Fundamentals", "REST API Design"]
    assert [i["title"] for i in store.list_sessions("alice", limit=2, offset=2, sort="alphabetical")[0]] == ["System Design Interview"]
    assert store.list_sessions("alice", status="in_progress")[1] == 3 and store.list_sessions("alice", status="completed")[1] == 0


def test_rename_and_duplicate_copy_content_without_touching_the_original(store):
    s = mk(store)
    store.save_section("alice", s["id"], "summary", "hello")
    assert store.update_session("alice", s["id"], title="  My ML  ")["title"] == "My ML"
    d = store.duplicate("alice", s["id"])
    assert d["id"] != s["id"] and d["title"] == "My ML (copy)" and d["completed_sections"] == 1
    store.save_section("alice", d["id"], "summary", "changed", force_new=True)
    assert store.get_section("alice", s["id"], "summary")["content"] == "hello"


def test_find_reusable_session_prevents_duplicates_but_new_session_is_possible(store):
    s = mk(store)
    assert store.find_reusable_session("alice", "  machine   learning ", "Intermediate")["id"] == s["id"]
    assert store.find_reusable_session("alice", "Machine Learning", "Advanced") is None
    assert store.find_reusable_session("bob", "Machine Learning", "Intermediate") is None
    store.archive("alice", s["id"])
    assert store.find_reusable_session("alice", "Machine Learning", "Intermediate") is None


# ------------------------------------------------------------------------------------------------- jobs
def test_exactly_one_job_per_generation_key_and_one_claimant_under_concurrency(store):
    s = mk(store)
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda _: store.create_or_get_job(s["id"], "summary", f"{s['id']}:summary:v1"), range(8)))
    assert sum(1 for _, created in res if created) == 1 and len({j["id"] for j, _ in res}) == 1
    jid = res[0][0]["id"]
    with ThreadPoolExecutor(8) as ex:
        wins = list(ex.map(lambda i: store.claim_job(jid, f"w{i}"), range(8)))
    assert wins.count(True) == 1
    assert store.get_session("alice", s["id"])["status"] == "generating"
    assert store.get_section("alice", s["id"], "summary")["status"] == "GENERATING"


def test_heartbeat_only_for_owner_and_finish_settles_state(store):
    s = mk(store)
    j, _ = store.create_or_get_job(s["id"], "summary", "k1")
    assert store.claim_job(j["id"], "w1")
    assert store.heartbeat(j["id"], "w1") and not store.heartbeat(j["id"], "w2")
    assert not store.finish_job(j["id"], "COMPLETED", worker="w2")
    assert store.finish_job(j["id"], "FAILED", worker="w1", error_code="model", error_message="boom")
    assert store.get_section("alice", s["id"], "summary")["status"] == "FAILED" and store.get_session("alice", s["id"])["status"] == "failed"
    assert store.requeue_job(j["id"]) and store.claim_job(j["id"], "w3")


def test_crashed_worker_is_recovered_and_completed_sections_are_preserved(store):
    s = mk(store)
    store.save_section("alice", s["id"], "summary", "done already")
    j, _ = store.create_or_get_job(s["id"], "deep_dive", "k-deep")
    t0 = T.utcnow()
    assert store.claim_job(j["id"], "dead-worker", lease_s=30, now=t0)
    assert not store.claim_job(j["id"], "other", now=t0 + timedelta(seconds=10))             # lease still live
    assert store.recover_abandoned(now=t0 + timedelta(seconds=10)) == 0
    assert store.recover_abandoned(now=t0 + timedelta(seconds=31)) == 1
    got = store.get_session("alice", s["id"])
    assert got["status"] == "active" and got["completed_sections"] == 1                      # not stuck in "generating"
    assert store.get_section("alice", s["id"], "summary")["content"] == "done already"
    assert store.get_section("alice", s["id"], "deep_dive")["status"] == "NOT_STARTED"
    assert store.get_job(j["id"])["status"] == "CANCELLED" and store.get_job(j["id"])["error_code"] == "interrupted"
    assert store.requeue_job(j["id"]) and store.claim_job(j["id"], "new-worker")


def test_expired_lease_can_be_taken_over_directly_but_attempts_are_capped(store):
    s = mk(store)
    j, _ = store.create_or_get_job(s["id"], "summary", "k2", max_attempts=2)
    t0 = T.utcnow()
    assert store.claim_job(j["id"], "w1", lease_s=5, now=t0)
    assert store.claim_job(j["id"], "w2", lease_s=5, now=t0 + timedelta(seconds=6))
    assert not store.claim_job(j["id"], "w3", now=t0 + timedelta(seconds=12))                # 2 attempts used
    assert store.recover_abandoned(now=t0 + timedelta(seconds=12)) == 1
    assert store.get_job(j["id"])["status"] == "FAILED" and store.get_section("alice", s["id"], "summary")["status"] == "FAILED"


def test_failed_regeneration_never_downgrades_completed_content(store):
    s = mk(store)
    store.save_section("alice", s["id"], "summary", "good")
    j, _ = store.create_or_get_job(s["id"], "summary", "regen-v2")
    assert store.claim_job(j["id"], "w")
    assert store.finish_job(j["id"], "FAILED", worker="w", error_code="x")
    sec = store.get_section("alice", s["id"], "summary")
    assert sec["status"] == "COMPLETED" and sec["content"] == "good" and sec["content_version"] == 1


def test_events_are_sequenced_and_resumable(store):
    s = mk(store)
    j, _ = store.create_or_get_job(s["id"], "summary", "k3")
    assert [store.append_event(j["id"], t) for t in ("started", "block", "completed")] == [1, 2, 3]
    evs = store.session_events("alice", s["id"])
    assert [e["event_type"] for e in evs] == ["started", "block", "completed"] and evs[0]["section_key"] == "summary"
    assert [e["event_type"] for e in store.session_events("alice", s["id"], after_id=evs[1]["id"])] == ["completed"]


def test_deleting_a_session_cancels_its_jobs_and_cascades_on_purge(db, store):
    s = mk(store)
    j, _ = store.create_or_get_job(s["id"], "summary", "k4")
    store.claim_job(j["id"], "w")
    store.delete("alice", s["id"])
    assert store.get_job(j["id"])["status"] == "CANCELLED"
    store.purge_deleted(timedelta(0), now=T.utcnow() + timedelta(seconds=1))
    assert store.get_job(j["id"]) is None


def test_feedback_is_user_scoped_and_validated_by_the_database(store):
    s = mk(store)
    assert store.add_feedback("alice", s["id"], section_key="summary", rating=4, comment="clear")
    assert [f["rating"] for f in store.feedback("alice", s["id"])] == [4] and store.feedback("bob", s["id"]) == []
    with pytest.raises(Exception):
        store.add_feedback("alice", s["id"], section_key=None, rating=9, comment=None)
