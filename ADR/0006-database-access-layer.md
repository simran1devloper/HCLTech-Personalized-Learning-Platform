# ADR 0006: Database access layer — SQLAlchemy 2.0 Core + Alembic, repository interfaces

**Status:** accepted · **Resolves:** TASKLIST I2.T1 · **Supersedes:** the hand-rolled SQLite migrator and raw-SQL repositories (kept only until migrated)

## Context
We need one data layer that runs on **SQLite** (local development and fast tests) and **PostgreSQL** (production), supports JSON documents, unique-constraint-based idempotency, safe job claiming for multiple workers, migrations with up *and* down scripts, and both synchronous API handlers and asynchronous workers/SSE. Today we have raw `sqlite3` with a global lock and a custom `PRAGMA user_version` migrator, which cannot run on PostgreSQL.

## Options considered
| Option | Dialect portability | JSON / upsert / locking | Migrations | Async + sync | Added weight | Verdict |
|---|---|---|---|---|---|---|
| A. **SQLAlchemy Core + Alembic** | Table metadata defined once; dialect-specific constructs where needed | `JSON.with_variant(JSONB)`, dialect `insert().on_conflict_*`, `with_for_update(skip_locked=True)` | Alembic up/down, SQLite batch mode for `ALTER` | Same Table objects for sync and async engines | ~7 packages, mature | **Chosen** |
| B. SQLAlchemy ORM | as A | as A | as A | as A | extra session/identity-map semantics; harder to reason about in workers | Rejected: not needed, adds magic |
| C. Thin driver layer (psycopg + sqlite3) + own migrator | We write placeholder, JSON, upsert and pooling shims ourselves | hand-written per dialect | we would build down-migrations, ordering, drift checks | two code paths | fewest dependencies, **most code to own and test** | Rejected: reinvents solved problems |
| D. SQLModel | via SQLAlchemy | via SQLAlchemy | Alembic | partial | couples models to Pydantic and ORM | Rejected: wrong abstraction for repositories |
| E. Query builders (e.g. pypika) + own migrator | partial | partial | own | own | little ecosystem | Rejected |

## Decision
1. **SQLAlchemy 2.0 Core** (not the ORM) defines the schema once as `Table` objects. SQL stays explicit and reviewable.
2. **Alembic** owns all schema changes. Every revision has `upgrade()` and `downgrade()`. Data migrations are separate revisions. Use expand/contract for breaking changes. CI runs `upgrade head → downgrade base → upgrade head` and `alembic check` (drift) on **both** dialects.
3. **Repository interfaces** (`typing.Protocol`) in the domain layer; implementations in `infrastructure/database/`. A **repository contract test suite is parametrised over SQLite and PostgreSQL**, so behaviour cannot diverge silently.
4. **Dialect-specific code is isolated** in small modules: job claiming uses `FOR UPDATE SKIP LOCKED` on PostgreSQL and `BEGIN IMMEDIATE` on SQLite (single writer); advisory/unique constraints provide request coalescing on both.
5. **Drivers:** `psycopg` 3 (sync and async) for PostgreSQL; stdlib `sqlite3` with `aiosqlite` for async SQLite. API handlers use a sync engine; workers and SSE use an async engine over the same metadata.
6. **Adopting existing databases:** the current v2 SQLite schema becomes Alembic baseline revision `0001`; databases with `user_version = 2` and no `alembic_version` are **stamped**, not recreated. The hand-written `001_learning_intelligence` PostgreSQL SQL (including the cycle-guard trigger) becomes Alembic revisions using `op.execute` for PostgreSQL-only objects.
7. The legacy Supabase-style adapter (`SQLiteClient`) stays until the legacy routes retire, then is removed.

## Consequences
- Positive: one schema definition, tested migrations in both directions, safe concurrency primitives, less custom infrastructure.
- Negative: new dependencies (`sqlalchemy`, `alembic`, `psycopg`, `aiosqlite`; dry-run resolves 7 packages on Python 3.12), a learning curve, and the need to keep dialect divergence visible and tested.
- Risks and mitigations: SQLite lacks `SKIP LOCKED` and true JSONB → isolated dialect modules plus contract tests on both; Alembic autogenerate is not trusted blindly → revisions are authored and reviewed.

## Evidence
`uv pip install --dry-run sqlalchemy alembic psycopg[binary] aiosqlite` resolved cleanly for the project's Python 3.12 environment (nothing installed). Licences (MIT/BSD/LGPL-family, to be recorded in `docs/licences.md` on adoption) must be checked at adoption time.
