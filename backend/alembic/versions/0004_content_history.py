"""persistent content history: sessions, sections, versions, jobs, events, tags, feedback

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
BigId = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "content_sessions",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("user_id", sa.String(64), nullable=False), sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.String(500), server_default="", nullable=False), sa.Column("topic", sa.String(200), nullable=False),
        sa.Column("normalized_topic", sa.String(200), nullable=False), sa.Column("content_type", sa.String(40), server_default="study_guide", nullable=False),
        sa.Column("learning_level", sa.String(20), nullable=False), sa.Column("language", sa.String(10), server_default="en", nullable=False),
        sa.Column("status", sa.String(12), server_default="active", nullable=False), sa.Column("total_sections", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completed_sections", sa.Integer(), server_default="0", nullable=False), sa.Column("metadata", JSON, nullable=False), sa.Column("context", JSON, nullable=False),
        sa.Column("search_text", sa.Text(), server_default="", nullable=False), sa.Column("created_at", TS, nullable=False), sa.Column("updated_at", TS, nullable=False),
        sa.Column("last_accessed_at", TS, nullable=True), sa.Column("archived_at", TS, nullable=True), sa.Column("deleted_at", TS, nullable=True),
        sa.CheckConstraint("status IN ('active','completed','generating','failed','archived')", name=op.f("ck_content_sessions_status_values")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_sessions")))
    with op.batch_alter_table("content_sessions") as b:
        b.create_index("idx_csessions_user_updated", ["user_id", "deleted_at", "updated_at"], unique=False)
        b.create_index("idx_csessions_user_topic", ["user_id", "normalized_topic", "learning_level"], unique=False)

    op.create_table(
        "content_sections",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("session_id", sa.String(36), nullable=False), sa.Column("section_key", sa.String(60), nullable=False),
        sa.Column("section_title", sa.String(120), nullable=False), sa.Column("status", sa.String(16), server_default="NOT_STARTED", nullable=False),
        sa.Column("content", JSON, nullable=True), sa.Column("content_hash", sa.String(64), nullable=True), sa.Column("content_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("prompt_version", sa.String(20), nullable=True), sa.Column("schema_version", sa.String(10), nullable=True), sa.Column("model_provider", sa.String(40), nullable=True),
        sa.Column("model_name", sa.String(120), nullable=True), sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False), sa.Column("generation_latency_ms", sa.Float(), server_default="0", nullable=False),
        sa.Column("created_at", TS, nullable=False), sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint("status IN ('NOT_STARTED','QUEUED','GENERATING','COMPLETED','FAILED')", name=op.f("ck_content_sections_status_values")),
        sa.ForeignKeyConstraint(["session_id"], ["content_sessions.id"], name=op.f("fk_content_sections_session_id_content_sessions"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_sections")), sa.UniqueConstraint("session_id", "section_key", name="uq_content_sections_key"))

    op.create_table(
        "content_section_versions",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("section_id", sa.String(36), nullable=False), sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("content", JSON, nullable=False), sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(20), nullable=True), sa.Column("schema_version", sa.String(10), nullable=True), sa.Column("model_provider", sa.String(40), nullable=True),
        sa.Column("model_name", sa.String(120), nullable=True), sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False), sa.Column("generation_latency_ms", sa.Float(), server_default="0", nullable=False),
        sa.Column("job_id", sa.String(36), nullable=True), sa.Column("created_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["section_id"], ["content_sections.id"], name=op.f("fk_content_section_versions_section_id_content_sections"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_section_versions")), sa.UniqueConstraint("section_id", "version", name="uq_content_section_versions_version"))
    with op.batch_alter_table("content_section_versions") as b:
        b.create_index("idx_csv_session", ["session_id"], unique=False)

    op.create_table(
        "content_generation_jobs",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("session_id", sa.String(36), nullable=False), sa.Column("section_id", sa.String(36), nullable=True),
        sa.Column("section_key", sa.String(60), nullable=False), sa.Column("generation_key", sa.String(160), nullable=False), sa.Column("status", sa.String(12), server_default="QUEUED", nullable=False),
        sa.Column("worker_id", sa.String(80), nullable=True), sa.Column("lease_expires_at", TS, nullable=True), sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False), sa.Column("started_at", TS, nullable=True), sa.Column("completed_at", TS, nullable=True),
        sa.Column("heartbeat_at", TS, nullable=True), sa.Column("error_code", sa.String(40), nullable=True), sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False), sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED')", name=op.f("ck_content_generation_jobs_status_values")),
        sa.ForeignKeyConstraint(["session_id"], ["content_sessions.id"], name=op.f("fk_content_generation_jobs_session_id_content_sessions"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["section_id"], ["content_sections.id"], name=op.f("fk_content_generation_jobs_section_id_content_sections"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_generation_jobs")), sa.UniqueConstraint("generation_key", name=op.f("uq_content_generation_jobs_generation_key")))
    with op.batch_alter_table("content_generation_jobs") as b:
        b.create_index("idx_cgjobs_session", ["session_id", "section_key"], unique=False)
        b.create_index("idx_cgjobs_status", ["status", "lease_expires_at"], unique=False)

    op.create_table(
        "content_generation_events",
        sa.Column("id", BigId, autoincrement=True, nullable=False), sa.Column("job_id", sa.String(36), nullable=False), sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False), sa.Column("payload", JSON, nullable=False), sa.Column("created_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["content_generation_jobs.id"], name=op.f("fk_content_generation_events_job_id_content_generation_jobs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_generation_events")), sa.UniqueConstraint("job_id", "event_sequence", name="uq_content_generation_events_seq"))

    op.create_table(
        "content_session_tags",
        sa.Column("session_id", sa.String(36), nullable=False), sa.Column("tag", sa.String(40), nullable=False), sa.Column("created_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["content_sessions.id"], name=op.f("fk_content_session_tags_session_id_content_sessions"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "tag", name=op.f("pk_content_session_tags")))

    op.create_table(
        "content_session_feedback",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("session_id", sa.String(36), nullable=False), sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("section_key", sa.String(60), nullable=True), sa.Column("rating", sa.Integer(), nullable=True), sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint("rating IS NULL OR rating BETWEEN 1 AND 5", name=op.f("ck_content_session_feedback_rating_range")),
        sa.ForeignKeyConstraint(["session_id"], ["content_sessions.id"], name=op.f("fk_content_session_feedback_session_id_content_sessions"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_session_feedback")))
    with op.batch_alter_table("content_session_feedback") as b:
        b.create_index("idx_csfeedback_session", ["session_id"], unique=False)


def downgrade() -> None:
    op.drop_table("content_session_feedback")
    op.drop_table("content_session_tags")
    op.drop_table("content_generation_events")
    op.drop_table("content_generation_jobs")
    op.drop_table("content_section_versions")
    op.drop_table("content_sections")
    op.drop_table("content_sessions")
