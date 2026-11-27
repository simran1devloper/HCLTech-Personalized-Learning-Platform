"""Schema definition (SQLAlchemy Core), defined once for SQLite and PostgreSQL.

Alembic revisions under ``backend/alembic`` are authored explicitly (immutable snapshots); a test compares the migrated
database with this metadata on both dialects so they cannot drift.

Legacy tables keep ISO-8601 text timestamps and JSON-as-text columns (compatible with existing data). Tables added for the
content platform use timezone-aware timestamps and real JSON (JSONB on PostgreSQL).
"""
from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

NAMING = {"ix": "ix_%(column_0_label)s", "uq": "uq_%(table_name)s_%(column_0_name)s", "ck": "ck_%(table_name)s_%(constraint_name)s",
          "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s", "pk": "pk_%(table_name)s"}
metadata = sa.MetaData(naming_convention=NAMING)

JSONType = sa.JSON().with_variant(JSONB(), "postgresql")
BigId = sa.BigInteger().with_variant(sa.Integer(), "sqlite")     # SQLite only autoincrements INTEGER primary keys


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every dialect (SQLite has no native tz-aware type)."""
    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


S = sa.String
# ---------------------------------------------------------------------------------------- legacy / learning tables
users = sa.Table(
    "users", metadata,
    sa.Column("id", S(64), primary_key=True), sa.Column("name", S(80), nullable=False), sa.Column("email", S(320), nullable=False, unique=True),
    sa.Column("password_hash", sa.Text, nullable=False), sa.Column("created_at", S(40), nullable=False),
    sa.CheckConstraint("length(name) BETWEEN 2 AND 80", name="name_length"))
learning_history = sa.Table(
    "learning_history", metadata,
    sa.Column("id", S(64), primary_key=True), sa.Column("user_id", S(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("topic", S(120), nullable=False), sa.Column("score", sa.Integer, nullable=False), sa.Column("level", S(20), nullable=False),
    sa.Column("created_at", S(40), nullable=False),
    sa.CheckConstraint("length(topic) BETWEEN 1 AND 120", name="topic_length"), sa.CheckConstraint("score BETWEEN 0 AND 10", name="score_range"),
    sa.CheckConstraint("level IN ('Beginner','Intermediate','Expert')", name="level_values"),
    sa.Index("idx_history_user", "user_id", "created_at"))
skills = sa.Table(
    "skills", metadata,
    sa.Column("skill_id", S(64), primary_key=True), sa.Column("slug", S(120), nullable=False, unique=True), sa.Column("name", S(200), nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=""), sa.Column("category", S(80), nullable=False, server_default="general"),
    sa.Column("domain", S(80), nullable=False, server_default="software"), sa.Column("difficulty", sa.Integer, nullable=False, server_default="2"),
    sa.Column("estimated_hours", sa.Float, nullable=False, server_default="10"), sa.Column("aliases", sa.Text, nullable=False, server_default="[]"),
    sa.Column("parent_skill_id", S(64), sa.ForeignKey("skills.skill_id", ondelete="SET NULL")),
    sa.Column("status", S(20), nullable=False, server_default="active"), sa.Column("created_at", S(40), nullable=False), sa.Column("updated_at", S(40), nullable=False),
    sa.CheckConstraint("difficulty BETWEEN 1 AND 5", name="difficulty_range"), sa.CheckConstraint("estimated_hours > 0", name="hours_positive"),
    sa.CheckConstraint("status IN ('active','deprecated','proposed')", name="status_values"), sa.Index("idx_skills_category", "category", "status"))
skill_dependencies = sa.Table(
    "skill_dependencies", metadata,
    sa.Column("skill_id", S(64), sa.ForeignKey("skills.skill_id", ondelete="CASCADE"), primary_key=True),
    sa.Column("prerequisite_id", S(64), sa.ForeignKey("skills.skill_id", ondelete="CASCADE"), primary_key=True),
    sa.Column("min_proficiency", sa.Float, nullable=False, server_default="2"),
    sa.CheckConstraint("min_proficiency BETWEEN 0 AND 5", name="min_proficiency_range"), sa.CheckConstraint("skill_id <> prerequisite_id", name="no_self_dependency"),
    sa.Index("idx_deps_prereq", "prerequisite_id"))
skill_evidence = sa.Table(
    "skill_evidence", metadata,
    sa.Column("evidence_id", BigId, primary_key=True, autoincrement=True), sa.Column("learner_id", S(64), nullable=False),
    sa.Column("skill_id", S(64), sa.ForeignKey("skills.skill_id"), nullable=False), sa.Column("kind", S(20), nullable=False),
    sa.Column("score", sa.Float, nullable=False), sa.Column("difficulty", sa.Float, nullable=False), sa.Column("n_items", sa.Integer, nullable=False, server_default="1"),
    sa.Column("source_id", S(64)), sa.Column("occurred_at", S(40), nullable=False),
    sa.CheckConstraint("score BETWEEN 0 AND 1", name="score_range"), sa.CheckConstraint("difficulty BETWEEN 0 AND 5", name="difficulty_range"),
    sa.Index("idx_evidence_learner", "learner_id", "skill_id", "occurred_at"),
    sa.Index("uq_evidence_source", "learner_id", "skill_id", "source_id", "kind", unique=True, sqlite_where=sa.text("source_id IS NOT NULL"),
             postgresql_where=sa.text("source_id IS NOT NULL")))
proficiency_snapshots = sa.Table(
    "proficiency_snapshots", metadata,
    sa.Column("snapshot_id", BigId, primary_key=True, autoincrement=True), sa.Column("learner_id", S(64), nullable=False), sa.Column("skill_id", S(64), nullable=False),
    sa.Column("proficiency_score", sa.Float, nullable=False), sa.Column("confidence_score", sa.Float, nullable=False), sa.Column("evidence_count", sa.Integer, nullable=False),
    sa.Column("proficiency_version", S(20), nullable=False), sa.Column("last_assessed_at", S(40)), sa.Column("snapshot_at", S(40), nullable=False),
    sa.Index("idx_snap", "learner_id", "skill_id", "snapshot_at"))
assessment_attempts = sa.Table(
    "assessment_attempts", metadata,
    sa.Column("attempt_id", S(64), primary_key=True), sa.Column("learner_id", S(64), nullable=False), sa.Column("skill_id", S(64), nullable=False),
    sa.Column("question_ids", sa.Text, nullable=False), sa.Column("status", S(20), nullable=False, server_default="open"),
    sa.Column("results", sa.Text, nullable=False, server_default="[]"), sa.Column("started_at", S(40), nullable=False), sa.Column("completed_at", S(40)),
    # revision 0005: persistent attempt metrics + idempotent creation + link to the learning plan generated from the attempt
    sa.Column("total_questions", sa.Integer, nullable=False, server_default="0"), sa.Column("answered_questions", sa.Integer, nullable=False, server_default="0"),
    sa.Column("correct_answers", sa.Integer, nullable=False, server_default="0"), sa.Column("score", sa.Float), sa.Column("difficulty_level", S(20)),
    sa.Column("submitted_at", UTCDateTime), sa.Column("created_at", UTCDateTime), sa.Column("idempotency_key", S(128)), sa.Column("learning_session_id", S(36)),
    # revision 0006: Diagnostic Study sessions share this table (kind='diagnostic'); generation_source records how the questions were obtained
    sa.Column("kind", S(16), nullable=False, server_default="assessment"), sa.Column("topic", S(200)), sa.Column("generation_source", S(16)),
    # revision 0009: persistent generation lifecycle (NULL = the attempt was served whole from the bank, no generation); status stays 'open' while generating
    sa.Column("requested_questions", sa.Integer), sa.Column("generation_status", S(16)), sa.Column("generation_error", S(200)), sa.Column("generation_updated_at", UTCDateTime),
    sa.Column("cancel_requested", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.CheckConstraint("status IN ('open','completed')", name="status_values"), sa.Index("idx_attempts_learner", "learner_id", "started_at"),
    sa.UniqueConstraint("learner_id", "idempotency_key", name="uq_attempts_idem"))
# Ordered, replayable lifecycle events of one assessment generation (SSE source of truth: reconnects and refreshes replay from here, any process can serve them).
assessment_generation_events = sa.Table(
    "assessment_generation_events", metadata,
    sa.Column("attempt_id", S(64), sa.ForeignKey("assessment_attempts.attempt_id", ondelete="CASCADE"), primary_key=True), sa.Column("seq", sa.Integer, primary_key=True),
    sa.Column("type", S(32), nullable=False), sa.Column("payload", JSONType, nullable=False), sa.Column("created_at", UTCDateTime, nullable=False))
learning_paths = sa.Table(
    "learning_paths", metadata,
    sa.Column("roadmap_id", S(64), primary_key=True), sa.Column("version", sa.Integer, primary_key=True), sa.Column("learner_id", S(64), nullable=False),
    sa.Column("body", sa.Text, nullable=False), sa.Column("created_at", S(40), nullable=False), sa.Index("idx_paths_learner", "learner_id"))
learning_goals = sa.Table(
    "learning_goals", metadata,
    sa.Column("goal_id", S(64), primary_key=True), sa.Column("learner_id", S(64), nullable=False), sa.Column("role_slug", S(80), nullable=False),
    sa.Column("hours_per_week", sa.Float, nullable=False), sa.Column("created_at", S(40), nullable=False), sa.Index("idx_goals_learner", "learner_id", "created_at"))
tutor_conversations = sa.Table(
    "tutor_conversations", metadata,
    sa.Column("conversation_id", S(64), primary_key=True), sa.Column("learner_id", S(64), nullable=False), sa.Column("title", S(200), nullable=False),
    sa.Column("skill_id", S(64)), sa.Column("language", S(10), nullable=False, server_default="en"), sa.Column("created_at", S(40), nullable=False),
    sa.Column("updated_at", S(40), nullable=False),
    sa.Column("pinned", sa.Boolean, nullable=False, server_default=sa.false()),     # revision 0010
    sa.Index("idx_conv_learner", "learner_id", "updated_at"))
tutor_messages = sa.Table(
    "tutor_messages", metadata,
    sa.Column("message_id", BigId, primary_key=True, autoincrement=True),
    sa.Column("conversation_id", S(64), sa.ForeignKey("tutor_conversations.conversation_id", ondelete="CASCADE"), nullable=False),
    sa.Column("role", S(20), nullable=False), sa.Column("content", sa.Text, nullable=False), sa.Column("created_at", S(40), nullable=False),
    # revision 0010: reply lifecycle (complete | streaming | cancelled | interrupted | failed | superseded) and the client's idempotency id of a question
    sa.Column("status", S(16), nullable=False, server_default="complete"), sa.Column("client_message_id", S(64)),
    sa.CheckConstraint("role IN ('user','assistant')", name="role_values"), sa.Index("idx_msgs_conv", "conversation_id", "message_id"),
    sa.Index("idx_msgs_client", "client_message_id"))
tutor_message_metadata = sa.Table(
    "tutor_message_metadata", metadata,
    sa.Column("message_id", BigId, sa.ForeignKey("tutor_messages.message_id", ondelete="CASCADE"), primary_key=True),
    sa.Column("provider", S(40)), sa.Column("model", S(120)), sa.Column("intent", S(40)), sa.Column("prompt_version", S(20)),
    sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"), sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
    sa.Column("tokens_estimated", sa.Boolean, nullable=False, server_default=sa.false()), sa.Column("first_token_ms", sa.Float), sa.Column("latency_ms", sa.Float),
    sa.Column("error_code", S(40)), sa.Column("details", JSONType, nullable=False))
tutor_feedback = sa.Table(
    "tutor_feedback", metadata,
    sa.Column("message_id", BigId, sa.ForeignKey("tutor_messages.message_id", ondelete="CASCADE"), primary_key=True),
    sa.Column("learner_id", S(64), primary_key=True), sa.Column("rating", sa.Integer, nullable=False), sa.Column("comment", sa.Text),
    sa.Column("created_at", UTCDateTime, nullable=False), sa.CheckConstraint("rating IN (-1, 1)", name="rating_values"))
tutor_tool_executions = sa.Table(
    "tutor_tool_executions", metadata,
    sa.Column("execution_id", S(36), primary_key=True), sa.Column("message_id", BigId, sa.ForeignKey("tutor_messages.message_id", ondelete="CASCADE"), nullable=False),
    sa.Column("learner_id", S(64), nullable=False), sa.Column("tool_name", S(60), nullable=False), sa.Column("status", S(16), nullable=False),
    sa.Column("args", JSONType, nullable=False), sa.Column("result", JSONType), sa.Column("created_at", UTCDateTime, nullable=False), sa.Column("updated_at", UTCDateTime, nullable=False),
    sa.CheckConstraint("status IN ('pending','running','succeeded','failed','dismissed')", name="status_values"),
    sa.Index("idx_tool_exec_message", "message_id"), sa.Index("idx_tool_exec_learner", "learner_id", "created_at"))

# ---------------------------------------------------------------------------------------- content platform tables (revision 0002)
JOB_STATES = ("NOT_STARTED", "QUEUED", "GENERATING", "VALIDATING", "COMPLETED", "FAILED", "CANCELLED")
_states = ",".join(f"'{s}'" for s in JOB_STATES)
topics = sa.Table(
    "topics", metadata,
    sa.Column("topic_id", S(64), primary_key=True), sa.Column("topic_norm", S(200), nullable=False, unique=True), sa.Column("display", S(200), nullable=False),
    sa.Column("domain", S(80), nullable=False, server_default=""), sa.Column("created_at", UTCDateTime, nullable=False))
courses = sa.Table(
    "courses", metadata,
    sa.Column("course_id", S(64), primary_key=True), sa.Column("course_key", S(64), nullable=False, unique=True),
    sa.Column("topic_id", S(64), sa.ForeignKey("topics.topic_id"), nullable=False), sa.Column("level", S(20), nullable=False),
    sa.Column("audience", S(80), nullable=False, server_default="general"), sa.Column("goal", S(200), nullable=False, server_default=""),
    sa.Column("language", S(10), nullable=False, server_default="en"), sa.Column("title", S(200), nullable=False), sa.Column("created_by", S(64)),
    sa.Column("created_at", UTCDateTime, nullable=False), sa.Column("updated_at", UTCDateTime, nullable=False))
sections = sa.Table(
    "sections", metadata,
    sa.Column("course_id", S(64), sa.ForeignKey("courses.course_id", ondelete="CASCADE"), primary_key=True), sa.Column("section_id", S(60), primary_key=True),
    sa.Column("status", S(20), nullable=False, server_default="NOT_STARTED"), sa.Column("current_version", sa.Integer),
    sa.Column("updated_at", UTCDateTime, nullable=False), sa.CheckConstraint(f"status IN ({_states})", name="status_values"))
content_versions = sa.Table(
    "content_versions", metadata,
    sa.Column("version_id", S(64), primary_key=True), sa.Column("course_id", S(64), nullable=False), sa.Column("section_id", S(60), nullable=False),
    sa.Column("content_version", sa.Integer, nullable=False), sa.Column("schema_version", S(10), nullable=False), sa.Column("prompt_version", S(20), nullable=False),
    sa.Column("model", S(120), nullable=False, server_default=""), sa.Column("provider", S(40), nullable=False, server_default=""),
    sa.Column("status", S(20), nullable=False, server_default="draft"), sa.Column("quality_status", S(20), nullable=False, server_default="unreviewed"),
    sa.Column("payload", JSONType), sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"), sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
    sa.Column("created_at", UTCDateTime, nullable=False), sa.Column("completed_at", UTCDateTime),
    sa.ForeignKeyConstraint(["course_id", "section_id"], ["sections.course_id", "sections.section_id"], ondelete="CASCADE", name="fk_content_versions_section"),
    sa.UniqueConstraint("course_id", "section_id", "content_version", name="uq_content_versions_version"),
    sa.CheckConstraint("status IN ('draft','complete','rejected')", name="status_values"))
content_blocks = sa.Table(
    "content_blocks", metadata,
    sa.Column("block_pk", BigId, primary_key=True, autoincrement=True),
    sa.Column("version_id", S(64), sa.ForeignKey("content_versions.version_id", ondelete="CASCADE"), nullable=False),
    sa.Column("ordinal", sa.Integer, nullable=False), sa.Column("block_id", S(64), nullable=False), sa.Column("type", S(20), nullable=False),
    sa.Column("payload", JSONType, nullable=False), sa.Column("created_at", UTCDateTime, nullable=False),
    sa.UniqueConstraint("version_id", "block_id", name="uq_content_blocks_block"), sa.UniqueConstraint("version_id", "ordinal", name="uq_content_blocks_ordinal"))
generation_jobs = sa.Table(
    "generation_jobs", metadata,
    sa.Column("job_id", S(64), primary_key=True), sa.Column("generation_key", S(64), nullable=False, unique=True),
    sa.Column("course_id", S(64), sa.ForeignKey("courses.course_id", ondelete="CASCADE"), nullable=False), sa.Column("section_id", S(60), nullable=False),
    sa.Column("status", S(20), nullable=False, server_default="QUEUED"), sa.Column("requested_by", S(64)), sa.Column("force", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"), sa.Column("lease_owner", S(80)), sa.Column("lease_expires_at", UTCDateTime),
    sa.Column("heartbeat_at", UTCDateTime), sa.Column("error", sa.Text), sa.Column("version_id", S(64)),
    sa.Column("created_at", UTCDateTime, nullable=False), sa.Column("updated_at", UTCDateTime, nullable=False), sa.Column("started_at", UTCDateTime), sa.Column("completed_at", UTCDateTime),
    sa.CheckConstraint(f"status IN ({_states})", name="status_values"), sa.Index("idx_jobs_status", "status", "lease_expires_at"), sa.Index("idx_jobs_course", "course_id", "section_id"))
generation_events = sa.Table(
    "generation_events", metadata,
    sa.Column("event_id", BigId, primary_key=True, autoincrement=True), sa.Column("job_id", S(64), sa.ForeignKey("generation_jobs.job_id", ondelete="CASCADE"), nullable=False),
    sa.Column("type", S(40), nullable=False), sa.Column("data", JSONType, nullable=False), sa.Column("created_at", UTCDateTime, nullable=False), sa.Index("idx_events_job", "job_id", "event_id"))
usage_ledger = sa.Table(
    "usage_ledger", metadata,
    sa.Column("ledger_id", BigId, primary_key=True, autoincrement=True), sa.Column("job_id", S(64), sa.ForeignKey("generation_jobs.job_id", ondelete="SET NULL")),
    sa.Column("course_id", S(64)), sa.Column("section_id", S(60)), sa.Column("provider", S(40), nullable=False, server_default=""), sa.Column("model", S(120), nullable=False, server_default=""),
    sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"), sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
    sa.Column("latency_ms", sa.Float, nullable=False, server_default="0"), sa.Column("cost_estimate", sa.Float, nullable=False, server_default="0"),
    sa.Column("outcome", S(20), nullable=False, server_default="ok"), sa.Column("created_at", UTCDateTime, nullable=False), sa.Index("idx_ledger_section", "course_id", "section_id"))
user_roles = sa.Table(
    "user_roles", metadata,
    sa.Column("user_id", S(64), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True), sa.Column("role", S(20), primary_key=True),
    sa.Column("granted_by", S(64)), sa.Column("granted_at", UTCDateTime, nullable=False), sa.CheckConstraint("role IN ('admin','reviewer','learner','rater')", name="role_values"))
audit_log = sa.Table(
    "audit_log", metadata,
    sa.Column("audit_id", BigId, primary_key=True, autoincrement=True), sa.Column("user_id", S(64)), sa.Column("action", S(80), nullable=False),
    sa.Column("entity", S(60)), sa.Column("entity_id", S(64)), sa.Column("details", JSONType), sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Index("idx_audit_user", "user_id", "created_at"))

# ---------------------------------------------------------------------------------------- idempotency (revision 0003)
idempotency_keys = sa.Table(
    "idempotency_keys", metadata,
    sa.Column("user_id", S(64), primary_key=True), sa.Column("route", S(120), primary_key=True), sa.Column("key", S(128), primary_key=True),
    sa.Column("status", S(12), nullable=False, server_default="pending"), sa.Column("response", JSONType),
    sa.Column("created_at", UTCDateTime, nullable=False), sa.Column("updated_at", UTCDateTime, nullable=False),
    sa.CheckConstraint("status IN ('pending','done')", name="status_values"))

# ---------------------------------------------------------------------------------------- persistent content history (revision 0004)
# One *user-owned* session per generated study guide. Distinct from the shared canonical ``courses``/``content_versions`` above:
# nothing personalised is ever written to those, and nothing here is readable without ``user_id`` matching.
SESSION_STATUSES = ("active", "completed", "generating", "failed", "archived")
SECTION_STATUSES = ("NOT_STARTED", "QUEUED", "GENERATING", "COMPLETED", "FAILED")
HISTORY_JOB_STATUSES = ("QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED")
_in = lambda vals: ",".join(f"'{v}'" for v in vals)  # noqa: E731
content_sessions = sa.Table(
    "content_sessions", metadata,
    sa.Column("id", S(36), primary_key=True), sa.Column("user_id", S(64), nullable=False), sa.Column("title", S(200), nullable=False),
    sa.Column("description", S(500), nullable=False, server_default=""), sa.Column("topic", S(200), nullable=False), sa.Column("normalized_topic", S(200), nullable=False),
    sa.Column("content_type", S(40), nullable=False, server_default="study_guide"), sa.Column("learning_level", S(20), nullable=False),
    sa.Column("language", S(10), nullable=False, server_default="en"), sa.Column("status", S(12), nullable=False, server_default="active"),
    sa.Column("total_sections", sa.Integer, nullable=False, server_default="0"), sa.Column("completed_sections", sa.Integer, nullable=False, server_default="0"),
    sa.Column("metadata", JSONType, nullable=False), sa.Column("context", JSONType, nullable=False), sa.Column("search_text", sa.Text, nullable=False, server_default=""),
    sa.Column("created_at", UTCDateTime, nullable=False), sa.Column("updated_at", UTCDateTime, nullable=False), sa.Column("last_accessed_at", UTCDateTime),
    sa.Column("archived_at", UTCDateTime), sa.Column("deleted_at", UTCDateTime),
    sa.CheckConstraint(f"status IN ({_in(SESSION_STATUSES)})", name="status_values"),
    sa.Index("idx_csessions_user_updated", "user_id", "deleted_at", "updated_at"), sa.Index("idx_csessions_user_topic", "user_id", "normalized_topic", "learning_level"))
content_sections = sa.Table(
    "content_sections", metadata,
    sa.Column("id", S(36), primary_key=True), sa.Column("session_id", S(36), sa.ForeignKey("content_sessions.id", ondelete="CASCADE"), nullable=False),
    sa.Column("section_key", S(60), nullable=False), sa.Column("section_title", S(120), nullable=False), sa.Column("status", S(16), nullable=False, server_default="NOT_STARTED"),
    sa.Column("content", JSONType), sa.Column("content_hash", S(64)), sa.Column("content_version", sa.Integer, nullable=False, server_default="0"),
    sa.Column("prompt_version", S(20)), sa.Column("schema_version", S(10)), sa.Column("model_provider", S(40)), sa.Column("model_name", S(120)),
    sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"), sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
    sa.Column("generation_latency_ms", sa.Float, nullable=False, server_default="0"),
    sa.Column("created_at", UTCDateTime, nullable=False), sa.Column("updated_at", UTCDateTime, nullable=False),
    sa.UniqueConstraint("session_id", "section_key", name="uq_content_sections_key"),
    sa.CheckConstraint(f"status IN ({_in(SECTION_STATUSES)})", name="status_values"))
content_section_versions = sa.Table(
    "content_section_versions", metadata,
    sa.Column("id", S(36), primary_key=True), sa.Column("section_id", S(36), sa.ForeignKey("content_sections.id", ondelete="CASCADE"), nullable=False),
    sa.Column("session_id", S(36), nullable=False), sa.Column("version", sa.Integer, nullable=False), sa.Column("content", JSONType, nullable=False),
    sa.Column("content_hash", S(64), nullable=False), sa.Column("prompt_version", S(20)), sa.Column("schema_version", S(10)), sa.Column("model_provider", S(40)),
    sa.Column("model_name", S(120)), sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"), sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
    sa.Column("generation_latency_ms", sa.Float, nullable=False, server_default="0"), sa.Column("job_id", S(36)), sa.Column("created_at", UTCDateTime, nullable=False),
    sa.UniqueConstraint("section_id", "version", name="uq_content_section_versions_version"), sa.Index("idx_csv_session", "session_id"))
content_generation_jobs = sa.Table(
    "content_generation_jobs", metadata,
    sa.Column("id", S(36), primary_key=True), sa.Column("session_id", S(36), sa.ForeignKey("content_sessions.id", ondelete="CASCADE"), nullable=False),
    sa.Column("section_id", S(36), sa.ForeignKey("content_sections.id", ondelete="CASCADE")), sa.Column("section_key", S(60), nullable=False),
    sa.Column("generation_key", S(160), nullable=False, unique=True), sa.Column("status", S(12), nullable=False, server_default="QUEUED"),
    sa.Column("worker_id", S(80)), sa.Column("lease_expires_at", UTCDateTime), sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"), sa.Column("started_at", UTCDateTime), sa.Column("completed_at", UTCDateTime),
    sa.Column("heartbeat_at", UTCDateTime), sa.Column("error_code", S(40)), sa.Column("error_message", sa.Text),
    sa.Column("created_at", UTCDateTime, nullable=False), sa.Column("updated_at", UTCDateTime, nullable=False),
    sa.CheckConstraint(f"status IN ({_in(HISTORY_JOB_STATUSES)})", name="status_values"),
    sa.Index("idx_cgjobs_session", "session_id", "section_key"), sa.Index("idx_cgjobs_status", "status", "lease_expires_at"))
content_generation_events = sa.Table(
    "content_generation_events", metadata,
    sa.Column("id", BigId, primary_key=True, autoincrement=True), sa.Column("job_id", S(36), sa.ForeignKey("content_generation_jobs.id", ondelete="CASCADE"), nullable=False),
    sa.Column("event_sequence", sa.Integer, nullable=False), sa.Column("event_type", S(40), nullable=False), sa.Column("payload", JSONType, nullable=False),
    sa.Column("created_at", UTCDateTime, nullable=False), sa.UniqueConstraint("job_id", "event_sequence", name="uq_content_generation_events_seq"))
content_session_tags = sa.Table(
    "content_session_tags", metadata,
    sa.Column("session_id", S(36), sa.ForeignKey("content_sessions.id", ondelete="CASCADE"), primary_key=True), sa.Column("tag", S(40), primary_key=True),
    sa.Column("created_at", UTCDateTime, nullable=False))
content_session_feedback = sa.Table(
    "content_session_feedback", metadata,
    sa.Column("id", S(36), primary_key=True), sa.Column("session_id", S(36), sa.ForeignKey("content_sessions.id", ondelete="CASCADE"), nullable=False),
    sa.Column("user_id", S(64), nullable=False), sa.Column("section_key", S(60)), sa.Column("rating", sa.Integer), sa.Column("comment", sa.Text),
    sa.Column("created_at", UTCDateTime, nullable=False), sa.CheckConstraint("rating IS NULL OR rating BETWEEN 1 AND 5", name="rating_range"),
    sa.Index("idx_csfeedback_session", "session_id"))


# ---------------------------------------------------------------------------------------- assessment question bank (revision 0005)
QUESTION_TYPES = ("single_choice", "multiple_select", "code_output", "debugging", "scenario", "conceptual", "practical")
COGNITIVE_LEVELS = ("remember", "understand", "apply", "analyze", "evaluate")
VALIDATION_STATUSES = ("pending", "validated", "rejected", "deprecated")
assessment_question_bank = sa.Table(
    "assessment_question_bank", metadata,
    sa.Column("id", S(36), primary_key=True), sa.Column("skill_id", S(64), sa.ForeignKey("skills.skill_id"), nullable=False),
    sa.Column("topic", S(120), nullable=False), sa.Column("subtopic", S(120), nullable=False), sa.Column("concept", S(120), nullable=False),
    sa.Column("question_text", sa.Text, nullable=False), sa.Column("question_type", S(20), nullable=False), sa.Column("difficulty", sa.Integer, nullable=False),
    sa.Column("options", JSONType, nullable=False), sa.Column("correct_answer", JSONType, nullable=False), sa.Column("explanation", sa.Text, nullable=False),
    sa.Column("solution_approach", sa.Text, nullable=False, server_default=""), sa.Column("practical_example", sa.Text, nullable=False, server_default=""),
    sa.Column("code_snippet", sa.Text), sa.Column("code_language", S(20)), sa.Column("tags", JSONType, nullable=False), sa.Column("cognitive_level", S(12), nullable=False),
    sa.Column("estimated_time_seconds", sa.Integer, nullable=False, server_default="60"), sa.Column("source_type", S(12), nullable=False, server_default="seed"),
    sa.Column("validation_status", S(12), nullable=False, server_default="pending"), sa.Column("quality_score", sa.Float, nullable=False, server_default="0"),
    sa.Column("version", sa.Integer, nullable=False, server_default="1"), sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("content_hash", S(64), nullable=False), sa.Column("created_at", UTCDateTime, nullable=False), sa.Column("updated_at", UTCDateTime, nullable=False),
    sa.UniqueConstraint("skill_id", "content_hash", name="uq_aqb_content"),
    sa.CheckConstraint(f"question_type IN ({_in(QUESTION_TYPES)})", name="type_values"), sa.CheckConstraint("difficulty BETWEEN 1 AND 5", name="difficulty_range"),
    sa.CheckConstraint(f"cognitive_level IN ({_in(COGNITIVE_LEVELS)})", name="cognitive_values"),
    sa.CheckConstraint(f"validation_status IN ({_in(VALIDATION_STATUSES)})", name="validation_values"),
    sa.Index("idx_aqb_select", "skill_id", "is_active", "validation_status", "difficulty"), sa.Index("idx_aqb_concept", "skill_id", "concept"))
assessment_attempt_questions = sa.Table(
    "assessment_attempt_questions", metadata,
    sa.Column("id", S(36), primary_key=True), sa.Column("attempt_id", S(64), sa.ForeignKey("assessment_attempts.attempt_id", ondelete="CASCADE"), nullable=False),
    sa.Column("question_id", S(64), nullable=False), sa.Column("question_snapshot", JSONType, nullable=False), sa.Column("displayed_order", sa.Integer, nullable=False),
    sa.Column("selected_answer", JSONType), sa.Column("is_correct", sa.Boolean), sa.Column("earned", sa.Float), sa.Column("time_taken_seconds", sa.Integer),
    sa.Column("submitted_at", UTCDateTime), sa.Column("answered_at", UTCDateTime),
    sa.UniqueConstraint("attempt_id", "question_id", name="uq_aaq_question"), sa.UniqueConstraint("attempt_id", "displayed_order", name="uq_aaq_order"))
assessment_concept_results = sa.Table(
    "assessment_concept_results", metadata,
    sa.Column("id", S(36), primary_key=True), sa.Column("attempt_id", S(64), sa.ForeignKey("assessment_attempts.attempt_id", ondelete="CASCADE"), nullable=False),
    sa.Column("user_id", S(64), nullable=False), sa.Column("skill_id", S(64), nullable=False), sa.Column("concept", S(120), nullable=False),
    sa.Column("questions_attempted", sa.Integer, nullable=False), sa.Column("correct_answers", sa.Float, nullable=False), sa.Column("accuracy", sa.Float, nullable=False),
    sa.Column("proficiency_level", S(20), nullable=False), sa.Column("identified_gap", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("created_at", UTCDateTime, nullable=False), sa.UniqueConstraint("attempt_id", "concept", name="uq_acr_concept"),
    sa.Index("idx_acr_user", "user_id", "skill_id", "created_at"))


# ---------------------------------------------------------------------------------------- question population jobs (revision 0006)
# One row per attempt to populate a skill's bank with generated questions. ``active`` is 1 while RUNNING and NULL afterwards, and UNIQUE(skill_id, active)
# therefore allows at most one running job per skill on every database (NULLs are distinct): that *is* the cross-process lock.
question_generation_jobs = sa.Table(
    "question_generation_jobs", metadata,
    sa.Column("id", S(36), primary_key=True), sa.Column("skill_id", S(64), sa.ForeignKey("skills.skill_id"), nullable=False), sa.Column("status", S(12), nullable=False, server_default="RUNNING"),
    sa.Column("active", sa.Integer), sa.Column("worker_id", S(80)), sa.Column("lease_expires_at", UTCDateTime), sa.Column("requested", sa.Integer, nullable=False, server_default="0"),
    sa.Column("added", sa.Integer, nullable=False, server_default="0"), sa.Column("rejected", sa.Integer, nullable=False, server_default="0"),
    sa.Column("llm_calls", sa.Integer, nullable=False, server_default="0"), sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
    sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"), sa.Column("error", S(200)), sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Column("completed_at", UTCDateTime), sa.UniqueConstraint("skill_id", "active", name="uq_qgj_one_running"),
    sa.CheckConstraint("status IN ('RUNNING','COMPLETED','FAILED')", name="status_values"), sa.Index("idx_qgj_skill", "skill_id", "created_at"))


# ---------------------------------------------------------------------------------------- study workspace state (revision 0007)
content_section_state = sa.Table(
    "content_section_state", metadata,
    sa.Column("session_id", S(36), sa.ForeignKey("content_sessions.id", ondelete="CASCADE"), primary_key=True), sa.Column("section_key", S(60), primary_key=True),
    sa.Column("understood", sa.Boolean, nullable=False, server_default=sa.false()), sa.Column("bookmarked", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("note", sa.Text, nullable=False, server_default=""), sa.Column("opened_at", UTCDateTime), sa.Column("updated_at", UTCDateTime, nullable=False))
