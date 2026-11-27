"""Supabase-client-compatible adapter so the legacy /auth and /learning code runs unchanged on SQLAlchemy.
Supports exactly the subset the legacy code uses: select(cols).eq().order(col, desc).limit().insert().execute()."""
from __future__ import annotations

import uuid

from sqlalchemy import insert, select

from app.infrastructure.database.engine import Db
from app.infrastructure.database.session import utc_iso
from app.infrastructure.database.tables import metadata


class _Result:
    def __init__(self, data: list[dict]):
        self.data = data


class _Query:
    def __init__(self, db: Db, table: str):
        if table not in metadata.tables:
            raise ValueError(f"unknown table: {table}")
        self.db, self.t = db, metadata.tables[table]
        self._cols = None
        self._where: list = []
        self._order = None
        self._limit: int | None = None
        self._insert: dict | None = None

    def _col(self, name: str):
        if name not in self.t.c:
            raise ValueError(f"unknown column {name!r} for {self.t.name}")
        return self.t.c[name]

    def select(self, cols: str = "*"):
        self._cols = None if cols.strip() == "*" else [self._col(c.strip()) for c in cols.split(",")]
        return self

    def eq(self, col, val):
        self._where.append(self._col(col) == val)
        return self

    def order(self, col, desc: bool = False):
        c = self._col(col)
        self._order = c.desc() if desc else c.asc()
        return self

    def limit(self, n: int):
        self._limit = int(n)
        return self

    def insert(self, row: dict):
        self._insert = dict(row)
        return self

    def execute(self) -> _Result:
        if self._insert is not None:
            row = self._insert
            for k in row:
                self._col(k)
            if "id" in self.t.c:
                row.setdefault("id", str(uuid.uuid4()))
            if "created_at" in self.t.c:
                row.setdefault("created_at", utc_iso())
            with self.db.begin() as c:
                c.execute(insert(self.t).values(**row))
            return _Result([row])
        q = select(*(self._cols if self._cols else [self.t])).where(*self._where)
        if self._order is not None:
            q = q.order_by(self._order)
        if self._limit is not None:
            q = q.limit(self._limit)
        with self.db.begin() as c:
            return _Result([dict(r._mapping) for r in c.execute(q)])


class LegacyDbClient:
    def __init__(self, db: Db):
        self.db = db

    def table(self, name: str) -> _Query:
        return _Query(self.db, name)
