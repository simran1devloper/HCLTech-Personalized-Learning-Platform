"""
utils/supabase_client.py — database access for the legacy /auth and /learning code.

DB_BACKEND=sqlite (default): a Supabase-client-compatible adapter over the local SQLite database.
DB_BACKEND=supabase:         the real Supabase client (requires the `supabase` package + credentials).
"""

import functools

from config import settings


def get_supabase():
    if settings.DB_BACKEND == "sqlite":
        from app.infrastructure.database.legacy_client import LegacyDbClient
        from app.infrastructure.database.session import get_database
        return LegacyDbClient(get_database())
    return _supabase_client()


@functools.lru_cache(maxsize=1)
def _supabase_client():
    from supabase import create_client  # imported lazily: not needed for the SQLite backend
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set when DB_BACKEND=supabase.")
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


def get_db():
    """FastAPI dependency."""
    return get_supabase()
