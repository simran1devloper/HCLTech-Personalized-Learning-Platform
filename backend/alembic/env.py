"""Alembic environment. The database URL comes from the Alembic config (set programmatically) or ``-x url=...``.

``-x tables=a,b,c`` restricts autogenerate to those tables; it is only used when authoring a new revision.
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

import sqlalchemy as sa
from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.infrastructure.database.tables import UTCDateTime, metadata  # noqa: E402

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
x = context.get_x_argument(as_dictionary=True)
provided = config.attributes.get("connection")   # the application passes its own connection (required for in-memory SQLite)
url = x.get("url") or config.get_main_option("sqlalchemy.url")
if not url and provided is None:
    raise RuntimeError("no database URL: set sqlalchemy.url programmatically or pass -x url=...")
only = {t for t in x.get("tables", "").split(",") if t}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and name == "alembic_version":
        return False
    if only and type_ == "table":
        return name in only
    return True


def render_item(type_, obj, autogen_context):
    # revisions must not import application code: render the custom type as its portable base type
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=metadata, include_object=include_object, render_item=render_item,
                      render_as_batch=connection.dialect.name == "sqlite", compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    if provided is not None:
        _run(provided)
        return
    connectable = sa.create_engine(url)
    with connectable.connect() as connection:
        _run(connection)
        connection.commit()
    connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=metadata, literal_binds=True, dialect_opts={"paramstyle": "named"}, render_item=render_item)
    with context.begin_transaction():
        context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
