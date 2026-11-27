"""Generation speed against the CONFIGURED provider (this calls the real model and may cost money).

    python -m benchmarks.content_speed [--reps 3] [--out ../docs/benchmarks/content-speed.md]

Compares the previous settings (4 guide sections at once, one quiz call) with the current ones. Uses an in-memory database and
neutral topics: no learner data is read or sent."""
from __future__ import annotations

import argparse
import asyncio
import difflib
import logging
import os
import statistics
import time

os.environ["DATABASE_PATH"] = ":memory:"

from app.core.config import get_settings  # noqa: E402
from app.core.container import get_container  # noqa: E402
from services import content_generation as cg  # noqa: E402

GUIDE_TOPICS = ["Apache Kafka", "PostgreSQL indexing", "Docker networking", "HTTP caching", "Git branching", "Redis persistence"]
QUIZ_TOPICS = ["Apache Airflow", "Nginx", "Terraform", "RabbitMQ", "Prometheus", "Ansible", "Elasticsearch", "GraphQL Federation"]


async def guide(topic: str, concurrency: int) -> dict:
    t0 = time.monotonic()
    first, ok, retries = None, 0, 0
    async for ev in cg.run_pipeline(topic, "Intermediate", 6, concurrency=concurrency):
        if ev["event"] == "chunk" and first is None:
            first = time.monotonic() - t0
        elif ev["event"] == "stage" and ev["data"].get("status") == "retrying" and ev["data"].get("detail", "").startswith("Retrying"):
            retries += 1
        elif ev["event"] == "section":
            ok += bool(ev["data"]["ok"])
    return {"first": first or float("nan"), "total": time.monotonic() - t0, "ok": ok, "retries": retries}


async def quiz(topic: str, parallel: int, n: int = 10) -> dict:
    get_settings().QUESTION_PARALLEL_BATCHES = parallel
    bank = get_container().bank
    skill = bank.resolve_or_register(topic)[0]
    t0 = time.monotonic()
    saved: list[float] = []
    res = await bank.generate_missing_questions(skill.name, skill.skill_id, n, concepts=[], on_batch=lambda k: saved.append(time.monotonic() - t0) if k else None)
    rows = get_container().assessments.candidates(skill.skill_id)
    texts = [q["question_text"] for q in get_container().assessments.full([r["id"] for r in rows]).values()]
    similar = sum(1 for i, a in enumerate(texts) for b in texts[i + 1:] if difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() > 0.85)
    return {"first": saved[0] if saved else float("nan"), "total": time.monotonic() - t0, "valid": res.added, "calls": res.llm_calls, "rejected": res.rejected,
            "near_duplicates": similar, "concepts": len({r["concept"] for r in rows})}


def med(rows: list[dict], key: str) -> float:
    return round(statistics.median(r[key] for r in rows), 1)


async def main(reps: int, out: str | None) -> None:
    logging.disable(logging.WARNING)
    s = get_settings()
    lines = [f"# Generation speed: providers `{s.LLM_PROVIDERS}`, model `{s.OPENROUTER_MODEL if s.LLM_PROVIDERS.startswith('openrouter') else '(see .env)'}`",
             f"{time.strftime('%Y-%m-%d %H:%M')}, {reps} runs per row, medians (range of totals in brackets). Seconds.", "",
             "## Study guide, 10 sections", "| Sections at once | First text | Complete | Sections valid | Retries |", "|---|---|---|---|---|"]
    topics = iter(GUIDE_TOPICS * reps)
    for conc in (4, s.CONTENT_SECTION_CONCURRENCY_CLOUD):
        rows = [await guide(next(topics), conc) for _ in range(reps)]
        lines.append(f"| {conc} | {med(rows, 'first')} | {med(rows, 'total')} ({min(r['total'] for r in rows):.1f}–{max(r['total'] for r in rows):.1f}) "
                     f"| {sum(r['ok'] for r in rows)}/{10 * reps} | {sum(r['retries'] for r in rows)} |")
        print(lines[-1], flush=True)
    lines += ["", "## Quiz, 10 new questions on a new topic", "| Simultaneous calls | First questions saved | All ready | Valid per run | Model calls | Rejected | Near-duplicate pairs | Concept areas |",
              "|---|---|---|---|---|---|---|---|"]
    topics = iter(QUIZ_TOPICS * reps)
    for parallel in (1, type(s).model_fields["QUESTION_PARALLEL_BATCHES"].default):
        rows = [await quiz(next(topics), parallel) for _ in range(reps)]
        lines.append(f"| {parallel} | {med(rows, 'first')} | {med(rows, 'total')} ({min(r['total'] for r in rows):.1f}–{max(r['total'] for r in rows):.1f}) "
                     f"| {', '.join(str(r['valid']) for r in rows)} | {', '.join(str(r['calls']) for r in rows)} | {sum(r['rejected'] for r in rows)} "
                     f"| {sum(r['near_duplicates'] for r in rows)} | {', '.join(str(r['concepts']) for r in rows)} |")
        print(lines[-1], flush=True)
    report = "\n".join(lines) + "\n"
    print("\n" + report)
    if out:
        with open(out, "w") as f:
            f.write(report)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out")
    a = ap.parse_args()
    asyncio.run(main(a.reps, a.out))
