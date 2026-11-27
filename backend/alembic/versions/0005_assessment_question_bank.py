"""assessment question bank, per-attempt question snapshots, concept results; extend assessment_attempts

Revision ID: 0005
Revises: 0004
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
TS = sa.DateTime(timezone=True)
Q = ("single_choice", "multiple_select", "code_output", "debugging", "scenario", "conceptual", "practical")
C = ("remember", "understand", "apply", "analyze", "evaluate")
V = ("pending", "validated", "rejected", "deprecated")
_in = lambda vals: ",".join(f"'{v}'" for v in vals)  # noqa: E731


def upgrade() -> None:
    # SQLite rebuilds the table for ALTER; a legacy (pre-Alembic) database has this CHECK unnamed, so re-declare it explicitly or it would be dropped
    keep = [sa.CheckConstraint("status IN ('open','completed')", name="ck_assessment_attempts_status_values")]
    insp = sa.inspect(op.get_bind())
    already_named = any(c.get("name") == "ck_assessment_attempts_status_values" for c in insp.get_check_constraints("assessment_attempts"))
    with op.batch_alter_table("assessment_attempts", table_args=[] if already_named or op.get_bind().dialect.name != "sqlite" else keep) as b:
        b.add_column(sa.Column("total_questions", sa.Integer(), server_default="0", nullable=False))
        b.add_column(sa.Column("answered_questions", sa.Integer(), server_default="0", nullable=False))
        b.add_column(sa.Column("correct_answers", sa.Integer(), server_default="0", nullable=False))
        b.add_column(sa.Column("score", sa.Float(), nullable=True))
        b.add_column(sa.Column("difficulty_level", sa.String(20), nullable=True))
        b.add_column(sa.Column("submitted_at", TS, nullable=True))
        b.add_column(sa.Column("created_at", TS, nullable=True))
        b.add_column(sa.Column("idempotency_key", sa.String(128), nullable=True))
        b.add_column(sa.Column("learning_session_id", sa.String(36), nullable=True))
        b.create_unique_constraint("uq_attempts_idem", ["learner_id", "idempotency_key"])

    op.create_table(
        "assessment_question_bank",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("skill_id", sa.String(64), nullable=False), sa.Column("topic", sa.String(120), nullable=False),
        sa.Column("subtopic", sa.String(120), nullable=False), sa.Column("concept", sa.String(120), nullable=False), sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("question_type", sa.String(20), nullable=False), sa.Column("difficulty", sa.Integer(), nullable=False), sa.Column("options", JSON, nullable=False),
        sa.Column("correct_answer", JSON, nullable=False), sa.Column("explanation", sa.Text(), nullable=False), sa.Column("solution_approach", sa.Text(), server_default="", nullable=False),
        sa.Column("practical_example", sa.Text(), server_default="", nullable=False), sa.Column("code_snippet", sa.Text(), nullable=True), sa.Column("code_language", sa.String(20), nullable=True),
        sa.Column("tags", JSON, nullable=False), sa.Column("cognitive_level", sa.String(12), nullable=False), sa.Column("estimated_time_seconds", sa.Integer(), server_default="60", nullable=False),
        sa.Column("source_type", sa.String(12), server_default="seed", nullable=False), sa.Column("validation_status", sa.String(12), server_default="pending", nullable=False),
        sa.Column("quality_score", sa.Float(), server_default="0", nullable=False), sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False), sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False), sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint(f"question_type IN ({_in(Q)})", name=op.f("ck_assessment_question_bank_type_values")),
        sa.CheckConstraint("difficulty BETWEEN 1 AND 5", name=op.f("ck_assessment_question_bank_difficulty_range")),
        sa.CheckConstraint(f"cognitive_level IN ({_in(C)})", name=op.f("ck_assessment_question_bank_cognitive_values")),
        sa.CheckConstraint(f"validation_status IN ({_in(V)})", name=op.f("ck_assessment_question_bank_validation_values")),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.skill_id"], name=op.f("fk_assessment_question_bank_skill_id_skills")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_question_bank")), sa.UniqueConstraint("skill_id", "content_hash", name="uq_aqb_content"))
    with op.batch_alter_table("assessment_question_bank") as b:
        b.create_index("idx_aqb_select", ["skill_id", "is_active", "validation_status", "difficulty"], unique=False)
        b.create_index("idx_aqb_concept", ["skill_id", "concept"], unique=False)

    op.create_table(
        "assessment_attempt_questions",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("attempt_id", sa.String(64), nullable=False), sa.Column("question_id", sa.String(64), nullable=False),
        sa.Column("question_snapshot", JSON, nullable=False), sa.Column("displayed_order", sa.Integer(), nullable=False), sa.Column("selected_answer", JSON, nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=True), sa.Column("earned", sa.Float(), nullable=True), sa.Column("time_taken_seconds", sa.Integer(), nullable=True),
        sa.Column("submitted_at", TS, nullable=True), sa.Column("answered_at", TS, nullable=True),
        sa.ForeignKeyConstraint(["attempt_id"], ["assessment_attempts.attempt_id"], name=op.f("fk_assessment_attempt_questions_attempt_id_assessment_attempts"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_attempt_questions")),
        sa.UniqueConstraint("attempt_id", "question_id", name="uq_aaq_question"), sa.UniqueConstraint("attempt_id", "displayed_order", name="uq_aaq_order"))

    op.create_table(
        "assessment_concept_results",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("attempt_id", sa.String(64), nullable=False), sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("skill_id", sa.String(64), nullable=False), sa.Column("concept", sa.String(120), nullable=False), sa.Column("questions_attempted", sa.Integer(), nullable=False),
        sa.Column("correct_answers", sa.Float(), nullable=False), sa.Column("accuracy", sa.Float(), nullable=False), sa.Column("proficiency_level", sa.String(20), nullable=False),
        sa.Column("identified_gap", sa.Boolean(), server_default=sa.false(), nullable=False), sa.Column("created_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["assessment_attempts.attempt_id"], name=op.f("fk_assessment_concept_results_attempt_id_assessment_attempts"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_concept_results")), sa.UniqueConstraint("attempt_id", "concept", name="uq_acr_concept"))
    with op.batch_alter_table("assessment_concept_results") as b:
        b.create_index("idx_acr_user", ["user_id", "skill_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_table("assessment_concept_results")
    op.drop_table("assessment_attempt_questions")
    op.drop_table("assessment_question_bank")
    with op.batch_alter_table("assessment_attempts") as b:
        b.drop_constraint("uq_attempts_idem", type_="unique")
        for c in ("learning_session_id", "idempotency_key", "created_at", "submitted_at", "difficulty_level", "score", "correct_answers", "answered_questions", "total_questions"):
            b.drop_column(c)
