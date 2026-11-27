"""Enable Row Level Security on every application table (PostgreSQL only)

Revision ID: 0011
Revises: 0010

Hosted PostgreSQL such as Supabase can expose the ``public`` schema through an HTTP API that anyone holding the publishable key
can call. With Row Level Security on and no policies, those API roles see no rows. The backend connects as the table owner, which
is not subject to Row Level Security, so the application is unaffected. A later revision that adds a table must enable it too
(``test_every_postgres_table_has_row_level_security`` fails otherwise). No-op on SQLite.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# only this application's tables: the schema may be shared with other applications
TABLES = (
    "alembic_version", "assessment_attempt_questions", "assessment_attempts", "assessment_concept_results", "assessment_generation_events",
    "assessment_question_bank", "audit_log", "content_blocks", "content_generation_events", "content_generation_jobs", "content_section_state",
    "content_section_versions", "content_sections", "content_session_feedback", "content_session_tags", "content_sessions", "content_versions",
    "courses", "generation_events", "generation_jobs", "idempotency_keys", "learning_goals", "learning_history", "learning_paths",
    "proficiency_snapshots", "question_generation_jobs", "sections", "skill_dependencies", "skill_evidence", "skills", "topics",
    "tutor_conversations", "tutor_feedback", "tutor_message_metadata", "tutor_messages", "tutor_tool_executions", "usage_ledger", "user_roles",
    "users",
)


def _set(action: str) -> None:
    if op.get_context().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" {action} ROW LEVEL SECURITY')


def upgrade() -> None:
    _set("ENABLE")


def downgrade() -> None:
    _set("DISABLE")
