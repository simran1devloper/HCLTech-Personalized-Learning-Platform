"""Assessment generation latency, before vs after, with a deterministic fake model (no network). Run from backend/:  python -m benchmarks.generation_bench [N] [SECONDS_PER_QUESTION]

before = what Start did for a skill with an empty bank: ``bank.ensure_questions`` blocks the request until generation ends; the learner sees nothing before that.
after  = ``POST /assessments`` (202) + persisted events: time to the first ``question_saved`` event and to ``completed``.
Two model shapes: 'local' (one question per call, like CPU Ollama) and 'hosted' (one batched call for all N)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time

N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
PER_Q = float(sys.argv[2]) if len(sys.argv) > 2 else 0.4
os.environ.update(ENVIRONMENT="test", SECRET_KEY="bench-secret-key-bench-secret-key-0123456789", GEMINI_API_KEY="t", OLLAMA_API_KEY="", RATE_LIMIT_PER_MINUTE="1000000",
                  DATABASE_PATH=os.path.join(tempfile.mkdtemp(), "bench.db"))
sys.path.insert(0, os.getcwd())
from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402

import main  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.container import get_container  # noqa: E402
from tests.integration.test_assessment_bank_service import fake_json  # noqa: E402

TOKEN = jwt.encode({"sub": "bench-user", "exp": int(time.time()) + 3600}, os.environ["SECRET_KEY"], algorithm="HS256")
H = {"Authorization": f"Bearer {TOKEN}"}
calls = {"n": 0, "tokens": 0}


async def fake(prompt):
    calls["n"] += 1
    k = int(prompt.split("exactly ")[1].split(" ")[0])
    await asyncio.sleep(PER_Q * k)                                         # generation time grows with the number of questions written
    calls["tokens"] += 150 * k
    return fake_json(k, base=f"B{calls['n']}"), 120, 150 * k


def run(shape: str, skill: str):
    get_settings().QUESTION_BATCH_SIZE = 1 if shape == "local" else 0
    calls.update(n=0, tokens=0)
    c = get_container()
    c.bank.call = fake
    sk = c.registry.resolve(skill, 1.0)
    # BEFORE: blocking inline generation (the old Start path for an empty bank)
    t0 = time.perf_counter()
    asyncio.run(c.bank.ensure_questions(sk.name, sk.skill_id, N, total=N))
    before_total, before_calls = time.perf_counter() - t0, calls["n"]
    with c.assessments.db.begin() as conn:                                   # empty the bank for this skill again: same starting point for 'after'
        from app.infrastructure.database import tables as T
        conn.execute(T.assessment_question_bank.delete().where(T.assessment_question_bank.c.skill_id == sk.skill_id))
    c.bank.invalidate()
    calls.update(n=0, tokens=0)
    # AFTER: 202 + persisted events
    with TestClient(main.app) as cl:
        c = get_container()
        c.bank.call = fake
        t0 = time.perf_counter()
        r = cl.post("/api/v1/assessments", json={"skill": skill, "n_questions": N}, headers=H)
        t_resp = time.perf_counter() - t0
        aid, first, done = r.json()["assessment_id"], None, None
        while done is None:
            st = cl.get(f"/api/v1/assessments/{aid}/generation", headers=H).json()
            if first is None and st["completed"] >= 1:
                first = time.perf_counter() - t0
            if st["status"] != "generating":
                done = time.perf_counter() - t0
            time.sleep(0.01)
        evs = c.assessments.events_after(aid, 0, 5000)
    saves = [e["payload"]["timestamp"] for e in evs if e["type"] == "question_saved"]
    print(f"{shape:7s} N={N} model={PER_Q}s/question | BEFORE: first visible result {before_total:6.2f}s (= total), model calls {before_calls}"
          f" | AFTER: POST {t_resp * 1000:5.0f} ms, first question {first:5.2f}s, completed {done:5.2f}s, model calls {calls['n']}, events {len(evs)}, saved {len(saves)}, tokens(out) {calls['tokens']}")


if __name__ == "__main__":
    run("local", "Kafka")
    run("hosted", "Deep Learning")
