"""Verify the Supabase (PostgreSQL) setup end to end:   python scripts/check_supabase.py   (run from backend/)

Uses the application's own settings (.env). It connects, creates or upgrades the tables, checks Row Level Security, then registers a
temporary user through the API, logs in, reads the data back and deletes that user. Never prints the connection string or password."""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from config import settings as legacy  # noqa: E402

EXAMPLE = "postgresql+psycopg://postgres.<project-ref>:<password>@<pooler-host>:5432/postgres"


def config_problem() -> str | None:
    s = get_settings()
    if s.STORE_BACKEND != "postgres":
        return "STORE_BACKEND is not 'postgres' in .env"
    if legacy.DB_BACKEND != "sqlite":
        return "set DB_BACKEND=sqlite (it then follows STORE_BACKEND); DB_BACKEND=supabase would split your data across two databases"
    if not s.DATABASE_URL:
        return f"DATABASE_URL is missing. In Supabase: Connect -> Session pooler. Format: {EXAMPLE}"
    if not s.DATABASE_URL.startswith("postgresql+psycopg://"):
        return "DATABASE_URL must start with postgresql+psycopg://"
    if any(c in s.DATABASE_URL for c in "<>[]"):
        return "DATABASE_URL still contains a placeholder such as <password> or [YOUR-PASSWORD]"
    # these two make the URL parse wrongly, and the connection error would then show part of the password
    if "aws-?-" in s.DATABASE_URL:
        return "replace the ? in aws-?- with the number in your Session pooler host (copy the host from Supabase)"
    if s.DATABASE_URL.count("@") > 1:
        return "the password contains @, so encode it: @ becomes %40, # becomes %23 (or reset it to letters and numbers only)"
    if any(c in s.DATABASE_URL.split("://", 1)[1].rsplit("@", 1)[0] for c in "#/?"):
        return "the password contains #, / or ?, so encode it: # becomes %23, / becomes %2F, ? becomes %3F"
    try:
        url = sa.engine.make_url(s.DATABASE_URL)
    except Exception:  # noqa: BLE001 - the message could contain the password
        return "DATABASE_URL cannot be parsed (URL-encode special characters in the password, e.g. @ becomes %40)"
    if url.port == 6543:
        return "use the Session pooler (port 5432), not the Transaction pooler (port 6543)"
    return None


def run_checks() -> None:
    from fastapi.testclient import TestClient

    from app.infrastructure.database.session import get_database
    import main

    db = get_database()                         # connects and migrates
    print("OK  connected; tables are up to date")
    with db.engine.connect() as c:
        unprotected = c.execute(sa.text(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relkind = 'r' AND NOT c.relrowsecurity ORDER BY 1")).scalars().all()
    if unprotected:
        raise RuntimeError(f"Row Level Security is off for: {', '.join(unprotected)}")
    print("OK  Row Level Security is on for every table")

    email = f"supabase-check-{uuid.uuid4().hex[:12]}@example.com"
    password = uuid.uuid4().hex + "Aa1!"
    try:
        with TestClient(main.app) as client:
            r = client.post("/auth/register", json={"name": "Connection Check", "email": email, "password": password})
            if r.status_code != 201:
                raise RuntimeError(f"register returned {r.status_code}")
            r = client.post("/auth/login", json={"email": email, "password": password})
            if r.status_code != 200:
                raise RuntimeError(f"login returned {r.status_code}")
            headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
            print("OK  register and login")
            for path in ("/learning/history", "/api/v1/skills", "/api/v1/careers", "/api/v1/analytics/summary",
                         "/api/v1/tutor/conversations", "/api/v1/content-sessions"):
                status = client.get(path, headers=headers).status_code
                if status != 200:
                    raise RuntimeError(f"GET {path} returned {status}")
            r = client.post("/api/v1/tutor/conversations", json={"title": "Connection check"}, headers=headers)
            if not r.is_success:
                raise RuntimeError(f"creating a conversation returned {r.status_code}")
            print("OK  reading and writing through the API")
    finally:
        from app.infrastructure.database.tables import metadata
        users, chats = metadata.tables["users"], metadata.tables["tutor_conversations"]
        with db.begin() as c:
            ids = c.execute(sa.select(users.c.id).where(users.c.email == email)).scalars().all()
            if ids:
                c.execute(sa.delete(chats).where(chats.c.learner_id.in_(ids)))
                c.execute(sa.delete(users).where(users.c.id.in_(ids)))
        print("OK  temporary test data removed")


def main_() -> int:
    problem = config_problem()
    if problem:
        print(f"FAILED: {problem}")
        return 2
    try:
        run_checks()
    except Exception as e:  # noqa: BLE001
        message = str(getattr(e, "orig", e)).strip().splitlines()
        secret = sa.engine.make_url(get_settings().DATABASE_URL).password
        text = message[0] if message else ""
        if secret:
            text = text.replace(str(secret), "***")
        print(f"FAILED: {type(e).__name__}: {text}")
        return 1
    print("\nSupabase is working.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())
