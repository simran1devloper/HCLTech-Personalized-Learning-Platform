"""SQLAlchemy engines for SQLite (dev/tests) and PostgreSQL (production). See ADR 0006.

* One synchronous engine serves API handlers; an asynchronous engine (aiosqlite / psycopg) serves workers and SSE.
* No module outside ``infrastructure/database`` imports a database driver.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool


def sqlite_url(path: str) -> str:
    return "sqlite+pysqlite:///:memory:" if path == ":memory:" else f"sqlite+pysqlite:///{path}"


def resolve_url(store_backend: str, database_path: str, database_url: str) -> str:
    if store_backend == "postgres":
        if not database_url:
            raise RuntimeError("STORE_BACKEND=postgres requires DATABASE_URL")
        return database_url
    return sqlite_url(database_path)


def _is_memory_sqlite(url: URL) -> bool:
    return url.get_backend_name() == "sqlite" and url.database in (None, "", ":memory:")


def create_sync_engine(url: str) -> Engine:
    u = make_url(url)
    if u.get_backend_name() == "sqlite":
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if _is_memory_sqlite(u):
            kwargs["poolclass"] = StaticPool           # one shared connection; serialised by Db's lock below
        eng = create_engine(u, **kwargs)

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _):                    # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            if not _is_memory_sqlite(u):
                cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
        return eng
    return create_engine(u, pool_pre_ping=True, pool_size=10, max_overflow=10)


def async_url(url: str) -> str:
    u = make_url(url)
    if u.get_backend_name() == "sqlite":
        return u.set(drivername="sqlite+aiosqlite").render_as_string(hide_password=False)
    if u.get_backend_name() == "postgresql":
        return u.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    raise ValueError(f"unsupported database: {u.get_backend_name()}")


def create_async_engine_for(url: str) -> AsyncEngine:
    return create_async_engine(async_url(url), pool_pre_ping=True)


class Db:
    """Thin handle around the sync engine. ``begin()`` yields a transactional Connection.

    An in-memory SQLite database is a single shared connection, so transactions are serialised with a lock; file
    SQLite and PostgreSQL use real connection pools."""

    def __init__(self, url: str):
        self.url = url
        self.engine = create_sync_engine(url)
        self.dialect = self.engine.dialect.name           # "sqlite" | "postgresql"
        self._lock = threading.RLock() if _is_memory_sqlite(make_url(url)) else None
        self._async: AsyncEngine | None = None

    @contextmanager
    def begin(self) -> Iterator[Connection]:
        if self._lock:
            with self._lock, self.engine.begin() as conn:
                yield conn
        else:
            with self.engine.begin() as conn:
                yield conn

    @property
    def async_engine(self) -> AsyncEngine:
        if self._async is None:
            self._async = create_async_engine_for(self.url)
        return self._async

    def close(self) -> None:
        self.engine.dispose()
