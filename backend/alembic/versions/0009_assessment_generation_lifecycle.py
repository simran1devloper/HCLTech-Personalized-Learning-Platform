"""assessment generation lifecycle: attempt generation columns + replayable event log

Revision ID: 0009
Revises: 0008
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
TS = sa.DateTime(timezone=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    named = any(c.get("name") == "ck_assessment_attempts_status_values" for c in insp.get_check_constraints("assessment_attempts"))
    keep = [] if named or op.get_bind().dialect.name != "sqlite" else [sa.CheckConstraint("status IN ('open','completed')", name="ck_assessment_attempts_status_values")]
    with op.batch_alter_table("assessment_attempts", table_args=keep) as b:
        b.add_column(sa.Column("requested_questions", sa.Integer(), nullable=True))
        b.add_column(sa.Column("generation_status", sa.String(16), nullable=True))
        b.add_column(sa.Column("generation_error", sa.String(200), nullable=True))
        b.add_column(sa.Column("generation_updated_at", TS, nullable=True))
        b.add_column(sa.Column("cancel_requested", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_table(
        "assessment_generation_events",
        sa.Column("attempt_id", sa.String(64), nullable=False), sa.Column("seq", sa.Integer(), nullable=False), sa.Column("type", sa.String(32), nullable=False),
        sa.Column("payload", JSON, nullable=False), sa.Column("created_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["assessment_attempts.attempt_id"], name=op.f("fk_assessment_generation_events_attempt_id_assessment_attempts"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("attempt_id", "seq", name=op.f("pk_assessment_generation_events")))


def downgrade() -> None:
    op.drop_table("assessment_generation_events")
    with op.batch_alter_table("assessment_attempts") as b:
        for c in ("cancel_requested", "generation_updated_at", "generation_error", "generation_status", "requested_questions"):
            b.drop_column(c)
