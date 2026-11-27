"""AI tutor: pinned conversations, message lifecycle, per-reply metadata, feedback and tool executions

Revision ID: 0010
Revises: 0009

Only adds columns and tables. ALTER TABLE is used directly (no batch mode): rebuilding tutor_conversations on SQLite would
cascade-delete its messages.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
TS = sa.DateTime(timezone=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
BigId = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.add_column("tutor_conversations", sa.Column("pinned", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("tutor_messages", sa.Column("status", sa.String(16), server_default="complete", nullable=False))
    op.add_column("tutor_messages", sa.Column("client_message_id", sa.String(64), nullable=True))
    op.create_index("idx_msgs_client", "tutor_messages", ["client_message_id"])
    op.create_table(
        "tutor_message_metadata",
        sa.Column("message_id", BigId, nullable=False), sa.Column("provider", sa.String(40)), sa.Column("model", sa.String(120)), sa.Column("intent", sa.String(40)),
        sa.Column("prompt_version", sa.String(20)), sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False), sa.Column("tokens_estimated", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("first_token_ms", sa.Float()), sa.Column("latency_ms", sa.Float()), sa.Column("error_code", sa.String(40)), sa.Column("details", JSON, nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["tutor_messages.message_id"], name=op.f("fk_tutor_message_metadata_message_id_tutor_messages"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("message_id", name=op.f("pk_tutor_message_metadata")))
    op.create_table(
        "tutor_feedback",
        sa.Column("message_id", BigId, nullable=False), sa.Column("learner_id", sa.String(64), nullable=False), sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text()), sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint("rating IN (-1, 1)", name="ck_tutor_feedback_rating_values"),
        sa.ForeignKeyConstraint(["message_id"], ["tutor_messages.message_id"], name=op.f("fk_tutor_feedback_message_id_tutor_messages"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("message_id", "learner_id", name=op.f("pk_tutor_feedback")))
    op.create_table(
        "tutor_tool_executions",
        sa.Column("execution_id", sa.String(36), nullable=False), sa.Column("message_id", BigId, nullable=False), sa.Column("learner_id", sa.String(64), nullable=False),
        sa.Column("tool_name", sa.String(60), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("args", JSON, nullable=False),
        sa.Column("result", JSON), sa.Column("created_at", TS, nullable=False), sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint("status IN ('pending','running','succeeded','failed','dismissed')", name="ck_tutor_tool_executions_status_values"),
        sa.ForeignKeyConstraint(["message_id"], ["tutor_messages.message_id"], name=op.f("fk_tutor_tool_executions_message_id_tutor_messages"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("execution_id", name=op.f("pk_tutor_tool_executions")))
    op.create_index("idx_tool_exec_message", "tutor_tool_executions", ["message_id"])
    op.create_index("idx_tool_exec_learner", "tutor_tool_executions", ["learner_id", "created_at"])


def downgrade() -> None:
    op.drop_table("tutor_tool_executions")
    op.drop_table("tutor_feedback")
    op.drop_table("tutor_message_metadata")
    op.drop_index("idx_msgs_client", table_name="tutor_messages")
    op.drop_column("tutor_messages", "client_message_id")
    op.drop_column("tutor_messages", "status")
    op.drop_column("tutor_conversations", "pinned")
