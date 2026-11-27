"""Process-wide database handle. Creates the engine from settings and brings the schema up to date with Alembic."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.dialects import postgresql as pg_dialect
from sqlalchemy.dialects import sqlite as sqlite_dialect

from app.infrastructure.database.engine import Db, resolve_url
from app.infrastructure.database.migrate import ensure_schema

log = logging.getLogger("database")
_db: Db | None = None


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_for(db: Db, table):
    """Dialect-specific INSERT construct (both support ON CONFLICT). Isolated here so repositories stay portable."""
    return (pg_dialect.insert if db.dialect == "postgresql" else sqlite_dialect.insert)(table)


def get_database() -> Db:
    global _db
    if _db is None:
        from app.core.config import get_settings
        s = get_settings()
        _db = Db(resolve_url(s.STORE_BACKEND, s.DATABASE_PATH, s.DATABASE_URL))
        how = ensure_schema(_db)
        if how != "current":
            log.info("database schema %s", how)
    return _db


def reset_database() -> None:
    global _db
    if _db is not None:
        _db.close()
    _db = None
