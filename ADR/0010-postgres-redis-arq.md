# ADR 0010: PostgreSQL + Redis + ARQ for durable generation

**Status:** accepted (decision D5) · **Context:** the baseline shows duplicate generation (80% wasted under 5 simultaneous identical requests), no survival across restarts, and no durable jobs.

## Decision
- **PostgreSQL** is the canonical store for courses, sections, blocks, atoms, jobs and events; SQLite remains for local development and tests behind repository interfaces (ADR 0006).
- **Redis** is acceleration and coordination only: L2 cache, ARQ broker, live event fan-out. It is never the only copy of content.
- **ARQ** is the job queue (async, Redis-based, fits FastAPI). No queue exists today, so no framework is duplicated. Celery was rejected as heavier; FastAPI `BackgroundTasks` and in-process tasks are not durable.
- **Events** are persisted in PostgreSQL with monotonic IDs; SSE replays from the table via `Last-Event-ID`; Redis pub/sub delivers live events with the table as the source of truth.
- **Coalescing** uses a unique generation key plus leases and heartbeats so one job serves all compatible requests and dead workers are recovered.

## Consequences
Two new services in local and CI environments (`docker-compose.dev.yml` provides them; verified healthy with pgvector 0.8.7); in-memory fallbacks for tests; operational runbooks in Iteration 8.
