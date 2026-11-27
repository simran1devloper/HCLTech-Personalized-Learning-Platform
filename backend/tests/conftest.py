import os

os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_PATH"] = ":memory:"
os.environ["SECRET_KEY"] = "test-secret-key-test-secret-key-0123456789"
os.environ["GEMINI_API_KEY"] = "test"
# tests must never read real credentials from a developer's .env or call real providers
os.environ["OLLAMA_API_KEY"] = ""
os.environ["OPENROUTER_API_KEY"] = ""   # a real key in a developer's .env must never make the tests call a paid API
os.environ["OPENAI_API_KEY"] = ""
os.environ["LLM_PROVIDERS"] = "ollama,gemini"   # the suite must not depend on the provider chosen in a developer's .env
os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_KEY"] = ""

import pytest
from fastapi.testclient import TestClient
from jose import jwt


def make_token(user_id: str, secret: str = os.environ["SECRET_KEY"], alg: str = "HS256", exp_delta: int = 3600) -> str:
    import time
    return jwt.encode({"sub": user_id, "exp": int(time.time()) + exp_delta}, secret, algorithm=alg)


@pytest.fixture
def client():
    from app.core.container import reset_container
    from app.core import security
    reset_container()
    security._limiter = None
    import main
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture
def live_client():
    """A client whose event loop stays alive between requests, so detached background tasks (assessment generation) keep running like they do under uvicorn."""
    from app.core.container import reset_container
    from app.core import security
    reset_container()
    security._limiter = None
    import main
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def auth():
    return lambda uid="user-a": {"Authorization": f"Bearer {make_token(uid)}"}


# ------------------------------------------------------------------ database fixtures (SQLite and PostgreSQL)
import uuid

import sqlalchemy as sa

PG_BASE = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://learnai:learnai_dev_only@127.0.0.1:5433/learnai")
REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6380/0")


def _pg_reachable() -> bool:
    try:
        with sa.create_engine(PG_BASE, connect_args={"connect_timeout": 2}).connect() as c:
            c.execute(sa.text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


def _redis_reachable() -> bool:
    try:
        import redis
        return bool(redis.Redis.from_url(REDIS_URL, socket_connect_timeout=1).ping())
    except Exception:  # noqa: BLE001
        return False


PG_OK, REDIS_OK = _pg_reachable(), _redis_reachable()
needs_postgres = pytest.mark.skipif(not PG_OK, reason="PostgreSQL not reachable (docker compose -f docker-compose.dev.yml up -d)")
needs_redis = pytest.mark.skipif(not REDIS_OK, reason="Redis not reachable (docker compose -f docker-compose.dev.yml up -d)")


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=needs_postgres)])
def db(request, tmp_path):
    """A migrated database on each dialect. PostgreSQL uses a throw-away schema per test."""
    from app.infrastructure.database.engine import Db
    from app.infrastructure.database.migrate import ensure_schema
    if request.param == "sqlite":
        handle = Db(f"sqlite+pysqlite:///{tmp_path}/t.db")
        ensure_schema(handle)
        yield handle
        handle.close()
        return
    schema = "t_" + uuid.uuid4().hex[:10]
    admin = sa.create_engine(PG_BASE, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f"CREATE SCHEMA {schema}"))
    handle = Db(f"{PG_BASE}{'&' if '?' in PG_BASE else '?'}options=-csearch_path%3D{schema}")
    ensure_schema(handle)
    yield handle
    handle.close()
    with admin.connect() as c:
        c.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))
    admin.dispose()


# ------------------------------------------------------------------ run the WHOLE suite on PostgreSQL:  TEST_STORE=postgres pytest
@pytest.fixture(autouse=True)
def _postgres_schema_per_test(monkeypatch):
    if os.environ.get("TEST_STORE") != "postgres":
        yield
        return
    from app.core.config import get_settings
    from app.core.container import reset_container
    schema = "t_" + uuid.uuid4().hex[:10]
    admin = sa.create_engine(PG_BASE, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f"CREATE SCHEMA {schema}"))
    monkeypatch.setenv("STORE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", f"{PG_BASE}{'&' if '?' in PG_BASE else '?'}options=-csearch_path%3D{schema}")
    get_settings.cache_clear()
    reset_container()
    yield
    reset_container()
    get_settings.cache_clear()
    with admin.connect() as c:
        c.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))
    admin.dispose()
