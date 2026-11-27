"""Diagnostic Study start: bank-hit latency, new-topic population (model calls, tokens), and duplicate generation prevented under concurrency.

    python -m benchmarks.diagnostic_bench sqlite|postgres [N]
The model is a fake that takes 300 ms and returns valid questions, so the numbers isolate the platform's own overhead and call counts (a real model adds its latency)."""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

backend = sys.argv[1] if len(sys.argv) > 1 else "sqlite"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 100
os.environ.update(ENVIRONMENT="test", SECRET_KEY="bench-secret-key-bench-secret-key-0123456789", GEMINI_API_KEY="t", OLLAMA_API_KEY="", RATE_LIMIT_PER_MINUTE="1000000")
if backend == "postgres":
    import sqlalchemy as sa
    base = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://learnai:learnai_dev_only@127.0.0.1:5433/learnai")
    schema = "dbench_" + uuid.uuid4().hex[:8]
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

calls = {"n": 0, "tok_in": 0, "tok_out": 0}
lock = threading.Lock()


async def fake(prompt):
    with lock:
        calls["n"] += 1
    await asyncio.sleep(0.3)
    n = int(prompt.split("exactly ")[1].split(" ")[0]); topic = prompt.split("about ")[1].split(" for ")[0]
    return json.dumps([{"concept": f"Area {i % 3}", "difficulty": 1 + i % 5, "question_type": "single_choice", "cognitive_level": "apply", "question_text": f"{topic} bench question {i}: which statement is accurate?",
                        "code_snippet": None, "options": [f"right {i}", f"a{i}", f"b{i}", f"c{i}"], "correct": [0], "explanation": f"Option right {i} is the accurate one."} for i in range(n)]), 150, 400


c = TestClient(main.app); box = get_container(); box.bank.call = fake
tok = lambda u: {"Authorization": "Bearer " + jwt.encode({"sub": u, "exp": int(time.time()) + 3600}, os.environ["SECRET_KEY"], algorithm="HS256")}  # noqa: E731
D = "/api/v1/diagnostic-study/start"
pct = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))]  # noqa: E731

hit = []
for i in range(N):                                                    # 1) existing topics: database only
    t = time.perf_counter(); r = c.post(D, json={"topic": ["Python", "SQL", "Docker", "Redis", "RAG"][i % 5], "question_count": 10}, headers=tok(f"u{i % 20}"))
    hit.append((time.perf_counter() - t) * 1000); assert r.status_code == 201 and r.json()["generation_source"] == "question_bank", r.text
bank_calls = calls["n"]

new = []
topics = [f"Benchtopic{i} Programming" for i in range(8)]
for tp in topics:                                                     # 2) new topics: generate once, then reuse
    t = time.perf_counter(); r = c.post(D, json={"topic": tp, "question_count": 10}, headers=tok("builder")); new.append((time.perf_counter() - t) * 1000)
    assert r.status_code == 201 and r.json()["generation_source"] == "ai_generated"
calls_after_new = calls["n"] - bank_calls
reuse = []
for i, tp in enumerate(topics * 5):
    t = time.perf_counter(); r = c.post(D, json={"topic": tp, "question_count": 10}, headers=tok(f"r{i}")); reuse.append((time.perf_counter() - t) * 1000)
    assert r.json()["generation_source"] == "question_bank"
calls_after_reuse = calls["n"] - bank_calls - calls_after_new

before = calls["n"]
barrier = threading.Barrier(10)                                       # 3) ten simultaneous learners, one brand-new topic


def go(i):
    barrier.wait(); return c.post(D, json={"topic": "Concurrent Topic Programming", "question_count": 10}, headers=tok(f"c{i}"))


with ThreadPoolExecutor(10) as ex:
    res = list(ex.map(go, range(10)))
st = box.assessments.generation_stats()
from app.observability.metrics import metrics  # noqa: E402
dups = sum(float(l.split()[-1]) for l in metrics.render().splitlines() if l.startswith("question_generation_duplicates_prevented_total"))
print(f"\nbackend={backend}")
print(f"existing topics   n={len(hit):3d}  p50={statistics.median(hit):6.1f} ms  p95={pct(hit, .95):6.1f} ms  model calls: {bank_calls}")
print(f"new topics        n={len(new):3d}  p50={statistics.median(new):6.0f} ms (includes the 300 ms fake model)  model calls: {calls_after_new} (1 per topic)")
print(f"repeat new topics n={len(reuse):3d}  p50={statistics.median(reuse):6.1f} ms  model calls: {calls_after_reuse}")
print(f"10 concurrent learners, 1 new topic: statuses={sorted({r.status_code for r in res})} model calls={calls['n'] - before} sessions={len({r.json()['session_id'] for r in res})} duplicates prevented={int(dups)}")
print(f"tokens per newly populated topic (fake model): {st['tokens_in'] // max(1, st['topics_populated'])} in / {st['tokens_out'] // max(1, st['topics_populated'])} out; jobs={st['jobs']} topics_populated={st['topics_populated']}")
total = N + len(topics) + len(reuse) + 10
print(f"bank hit rate: {(N + len(reuse)) / total:.1%}  AI fallback rate: {(len(topics) + 1) / total:.1%} of {total} starts")
