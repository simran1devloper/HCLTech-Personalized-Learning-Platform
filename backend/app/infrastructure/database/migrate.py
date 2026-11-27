"""Run Alembic programmatically and adopt databases created before Alembic (SQLite ``PRAGMA user_version`` era)."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.infrastructure.database.engine import Db

BACKEND_DIR = Path(__file__).resolve().parents[3]
BASELINE = "0001"


def alembic_config(url: str, connection=None) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def current_revision(db: Db) -> str | None:
    with db.engine.connect() as c:
        if "alembic_version" not in inspect(c).get_table_names():
            return None
        return c.execute(text("SELECT version_num FROM alembic_version")).scalar()


def ensure_schema(db: Db) -> str:
    """Bring the database to the latest revision. Returns how: 'fresh' | 'adopted' | 'upgraded' | 'current'.

    * No tables at all  -> upgrade from scratch.
    * Pre-Alembic database (legacy tables present, no alembic_version; SQLite ``user_version`` = 2) -> **stamp** the baseline
      (the data is kept as is, never recreated) and then upgrade.
    The application's own connection is passed to Alembic so in-memory SQLite and pooled databases behave identically.
    """
    before = current_revision(db)
    with db.engine.connect() as c:
        tables = set(inspect(c).get_table_names())
    adopt = before is None and "users" in tables and "skills" in tables
    with db.begin() as conn:
        cfg = alembic_config(db.url, conn)
        if adopt:
            command.stamp(cfg, BASELINE)
        command.upgrade(cfg, "head")
    if adopt:
        return "adopted"
    after = current_revision(db)
    return "fresh" if before is None else ("current" if after == before else "upgraded")
