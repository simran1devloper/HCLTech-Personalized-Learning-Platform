"""diagnostic study: attempt kind/topic/generation_source; question population jobs (cross-process generation lock)

Revision ID: 0006
Revises: 0005
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    named = any(c.get("name") == "ck_assessment_attempts_status_values" for c in insp.get_check_constraints("assessment_attempts"))
    keep = [] if named or op.get_bind().dialect.name != "sqlite" else [sa.CheckConstraint("status IN ('open','completed')", name="ck_assessment_attempts_status_values")]
    with op.batch_alter_table("assessment_attempts", table_args=keep) as b:
        b.add_column(sa.Column("kind", sa.String(16), server_default="assessment", nullable=False))
        b.add_column(sa.Column("topic", sa.String(200), nullable=True))
        b.add_column(sa.Column("generation_source", sa.String(16), nullable=True))
    op.create_table(
        "question_generation_jobs",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("skill_id", sa.String(64), nullable=False), sa.Column("status", sa.String(12), server_default="RUNNING", nullable=False),
        sa.Column("active", sa.Integer(), nullable=True), sa.Column("worker_id", sa.String(80), nullable=True), sa.Column("lease_expires_at", TS, nullable=True),
        sa.Column("requested", sa.Integer(), server_default="0", nullable=False), sa.Column("added", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rejected", sa.Integer(), server_default="0", nullable=False), sa.Column("llm_calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tokens_in", sa.Integer(), server_default="0", nullable=False), sa.Column("tokens_out", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.String(200), nullable=True), sa.Column("created_at", TS, nullable=False), sa.Column("completed_at", TS, nullable=True),
        sa.CheckConstraint("status IN ('RUNNING','COMPLETED','FAILED')", name=op.f("ck_question_generation_jobs_status_values")),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.skill_id"], name=op.f("fk_question_generation_jobs_skill_id_skills")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_question_generation_jobs")), sa.UniqueConstraint("skill_id", "active", name="uq_qgj_one_running"))
    with op.batch_alter_table("question_generation_jobs") as b:
        b.create_index("idx_qgj_skill", ["skill_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_table("question_generation_jobs")
    with op.batch_alter_table("assessment_attempts") as b:
        for c in ("generation_source", "topic", "kind"):
            b.drop_column(c)
