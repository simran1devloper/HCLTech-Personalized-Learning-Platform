"""database-level guard against cyclic skill dependencies

``skill_dependencies`` already forbids self-dependencies (CHECK). A CHECK cannot see other rows, so cycles of length >= 2
are rejected by a trigger that walks the prerequisite chain with a recursive query before every INSERT/UPDATE.
SQLite and PostgreSQL get equivalent triggers; the application layer (SkillGraph.add_edge) still checks first and
returns a friendly error, so this is the last line of defence for any other writer (scripts, migrations, consoles).

Revision ID: 0008
Revises: 0007
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SQLITE = """
CREATE TRIGGER trg_skill_dependencies_no_cycle_{ev}
BEFORE {ev} ON skill_dependencies
WHEN EXISTS (
  WITH RECURSIVE up(id) AS (
    SELECT NEW.prerequisite_id
    UNION
    SELECT d.prerequisite_id FROM skill_dependencies d JOIN up ON d.skill_id = up.id
  )
  SELECT 1 FROM up WHERE id = NEW.skill_id
)
BEGIN
  SELECT RAISE(ABORT, 'skill dependency would create a cycle');
END
"""

_PG_FN = """
CREATE OR REPLACE FUNCTION skill_dependencies_no_cycle() RETURNS trigger AS $$
BEGIN
  IF EXISTS (
    WITH RECURSIVE up(id) AS (
      SELECT NEW.prerequisite_id
      UNION
      SELECT d.prerequisite_id FROM skill_dependencies d JOIN up ON d.skill_id = up.id
    )
    SELECT 1 FROM up WHERE id = NEW.skill_id
  ) THEN
    RAISE EXCEPTION 'skill dependency would create a cycle' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(_PG_FN)
        op.execute("CREATE TRIGGER trg_skill_dependencies_no_cycle BEFORE INSERT OR UPDATE ON skill_dependencies "
                   "FOR EACH ROW EXECUTE FUNCTION skill_dependencies_no_cycle()")
    else:
        for ev in ("INSERT", "UPDATE"):
            op.execute(_SQLITE.format(ev=ev))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_skill_dependencies_no_cycle ON skill_dependencies")
        op.execute("DROP FUNCTION IF EXISTS skill_dependencies_no_cycle()")
    else:
        for ev in ("INSERT", "UPDATE"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_skill_dependencies_no_cycle_{ev}")
