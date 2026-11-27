"""study workspace: per-section understood / bookmark / note state

Revision ID: 0007
Revises: 0006
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite cannot roll DDL back: an upgrade interrupted after this table was created would otherwise fail on every restart ("table already exists")
    if "content_section_state" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "content_section_state",
        sa.Column("session_id", sa.String(36), nullable=False), sa.Column("section_key", sa.String(60), nullable=False),
        sa.Column("understood", sa.Boolean(), server_default=sa.false(), nullable=False), sa.Column("bookmarked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False), sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["content_sessions.id"], name=op.f("fk_content_section_state_session_id_content_sessions"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "section_key", name=op.f("pk_content_section_state")))


def downgrade() -> None:
    op.drop_table("content_section_state")
