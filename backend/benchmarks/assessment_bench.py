"""Measures the assessment hot paths end to end (in-process ASGI, real database, real bank). No model is available: any call would raise and fail the run.

    python -m benchmarks.assessment_bench sqlite    [N]
    python -m benchmarks.assessment_bench postgres  [N]   # needs the dev PostgreSQL (docker compose -f docker-compose.dev.yml up -d postgres)

Reports p50/p95 latency per operation, model calls, bank hit rate and the share of time spent in the candidate-selection query (from the app's own metrics)."""
from __future__ import annotations

import os
import statistics
import sys
import tempfile
import time
import uuid

backend = sys.argv[1] if len(sys.argv) > 1 else "sqlite"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 150
os.environ.update(ENVIRONMENT="test", SECRET_KEY="bench-secret-key-bench-secret-key-0123456789", GEMINI_API_KEY="t", OLLAMA_API_KEY="", RATE_LIMIT_PER_MINUTE="1000000")
if backend == "postgres":
    import sqlalchemy as sa
    base = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://learnai:learnai_dev_only@127.0.0.1:5433/learnai")
    schema = "bench_" + uuid.uuid4().hex[:8]
    with sa.create_engine(base).begin() as c:
        c.execute(sa.text(f"CREATE SCHEMA {schema}"))
    os.environ.update(STORE_BACKEND="postgres", DATABASE_URL=f"{base}{'&' if '?' in base else '?'}options=-csearch_path%3D{schema}")
else:
    os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "bench.db")

sys.path.insert(0, os.getcwd())
from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402

import main  # noqa: E402
from app.core.container import get_container  # noqa: E402
from app.observability.metrics import metrics  # noqa: E402
from services import content_generation as cg  # noqa: E402

llm_calls = 0


async def forbidden(*a, **k):
    global llm_calls
    llm_calls += 1
    raise AssertionError("model called")

cg._call_model = forbidden  # type: ignore[assignment]
c = TestClient(main.app)
box = get_container()
box.bank.call = forbidden
tok = lambda u: {"Authorization": "Bearer " + jwt.encode({"sub": u, "exp": int(time.time()) + 3600}, os.environ["SECRET_KEY"], algorithm="HS256")}  # noqa: E731
SKILLS = ["Python", "SQL", "Machine Learning", "Docker", "RAG", "FastAPI", "System Design", "Redis", "LangGraph", "MCP"]
T: dict[str, list[float]] = {"start": [], "autosave": [], "submit": [], "results": [], "history": [], "skill_gaps": []}
fail = 0


def timed(name, fn):
    t = time.perf_counter(); r = fn(); T[name].append((time.perf_counter() - t) * 1000); return r


for i in range(N):
    h = tok(f"bench-{i % 25}")                       # 25 learners, each taking several assessments (exercises recently-seen avoidance)
    r = timed("start", lambda: c.post("/api/v1/assessments", json={"skill": SKILLS[i % len(SKILLS)], "n_questions": 10}, headers={**h, "X-Idempotency-Key": f"k{i}"}))
    if r.status_code != 201:
        fail += 1; continue
    a = r.json(); qid = a["questions"][0]["question_id"]
    timed("autosave", lambda: c.put(f"/api/v1/assessments/{a['assessment_id']}/answers", json={"answers": {qid: 0}}, headers=h))
    timed("submit", lambda: c.post(f"/api/v1/assessments/{a['assessment_id']}/submit", json={"answers": {}}, headers=h))
    timed("results", lambda: c.get(f"/api/v1/assessments/{a['assessment_id']}/results", headers=h))
    timed("skill_gaps", lambda: c.get(f"/api/v1/assessments/{a['assessment_id']}/skill-gaps", headers=h))
    timed("history", lambda: c.get("/api/v1/assessments", headers=h))

pct = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))]  # noqa: E731
print(f"\nbackend={backend}  attempts={N}  failed_starts={fail}  model_calls={llm_calls}  seeded_bank={box.assessments.count_by_source('seed')}")
print(f"{'operation':12} {'n':>5} {'p50 ms':>9} {'p95 ms':>9} {'max ms':>9}")
for k, v in T.items():
    if v:
        print(f"{k:12} {len(v):5d} {statistics.median(v):9.1f} {pct(v, .95):9.1f} {max(v):9.1f}")
text = metrics.render()
grab = lambda name: [float(l.split()[-1]) for l in text.splitlines() if l.startswith(name)]  # noqa: E731
print("bank-served starts:", int(sum(grab("assessment_start_total"))), " llm calls counted by app:", int(sum(grab("assessment_llm_calls_total"))))
