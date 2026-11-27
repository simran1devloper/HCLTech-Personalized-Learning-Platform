"""Measure the v2 content pipeline against the *configured real provider* (Ollama / Gemini / ...).

    cd backend && python -m benchmarks.live_streaming --reps 5 --topic "Python decorators" --out ../docs/benchmarks

Reports, per run and aggregated: time to first chunk, time to first complete section, total time, per-section success,
attempts used (retries / repairs / provider failovers show up as attempt > 1) and token usage reported by the provider.
Use the same command with CONTENT_STREAMING=false CONTENT_NATIVE_SCHEMA=false CONTENT_FEWSHOT=false CONTENT_MERMAID=false
for the "v1" comparison row (set --label accordingly).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import statistics
import sys
import time
from pathlib import Path

from services import content_generation as cg


async def one_run(topic: str, level: str, score: int) -> dict:
    t0 = time.perf_counter()
    first_chunk = first_section = None
    ok: dict[str, bool] = {}
    deferred: list[str] = []
    attempts: dict[str, int] = {}
    tokens_out = 0
    async for ev in cg.run_pipeline(topic, level, score):
        now = time.perf_counter() - t0
        d = ev["data"]
        if ev["event"] == "chunk" and first_chunk is None:
            first_chunk = now
        elif ev["event"] == "section":
            if d.get("skipped"):          # deferred by the time budget: not a failure, generate on demand
                deferred.append(d["name"])
                continue
            ok[d["name"]] = bool(d["ok"])
            if d["ok"] and first_section is None:
                first_section = now
        elif ev["event"] == "stage" and str(d.get("id", "")).startswith("section:"):
            name = d["id"][8:]
            attempts[name] = max(attempts.get(name, 1), int(d.get("attempt") or 1))
            if d.get("status") == "done":
                tokens_out += int(d.get("tokens_out") or 0)
    return {"total_s": time.perf_counter() - t0, "first_chunk_s": first_chunk, "first_section_s": first_section,
            "sections_ok": sum(ok.values()), "sections": len(ok), "attempts": attempts,
            "retried_sections": sorted(n for n, a in attempts.items() if a > 1), "failed_sections": sorted(n for n, v in ok.items() if not v),
            "deferred_sections": sorted(deferred),
            "tokens_out": tokens_out}


def _pct(xs: list[float], p: float) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    return None if not xs else xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def summarise(runs: list[dict]) -> dict:
    sec = sum(r["sections"] for r in runs) or 1
    return {
        "runs": len(runs),
        "section_success_rate": round(sum(r["sections_ok"] for r in runs) / sec, 4),
        "guides_fully_complete": sum(1 for r in runs if not r["failed_sections"]),
        "deferred_sections_per_guide": round(sum(len(r.get("deferred_sections", [])) for r in runs) / len(runs), 2),
        "retried_sections_per_guide": round(sum(len(r["retried_sections"]) for r in runs) / len(runs), 2),
        "first_chunk_s": {"p50": _pct([r["first_chunk_s"] for r in runs], .5), "p95": _pct([r["first_chunk_s"] for r in runs], .95)},
        "first_section_s": {"p50": _pct([r["first_section_s"] for r in runs], .5), "p95": _pct([r["first_section_s"] for r in runs], .95)},
        "total_s": {"p50": _pct([r["total_s"] for r in runs], .5), "p95": _pct([r["total_s"] for r in runs], .95),
                    "mean": round(statistics.mean(r["total_s"] for r in runs), 2)},
    }


def to_markdown(meta: dict, summary: dict, runs: list[dict]) -> str:
    f = lambda v: "n/a" if v is None else f"{v:.1f}"  # noqa: E731
    rows = "\n".join(f"| {i + 1} | {f(r['total_s'])} | {f(r['first_chunk_s'])} | {f(r['first_section_s'])} | {r['sections_ok']}/{r['sections']} | "
                     f"{', '.join(r['retried_sections']) or '-'} | {', '.join(r['failed_sections']) or '-'} | {', '.join(r.get('deferred_sections', [])) or '-'} |" for i, r in enumerate(runs))
    return (f"# Live content generation benchmark — {meta['label']}\n\n"
            f"Date {meta['date']} · topic `{meta['topic']}` · level {meta['level']} · providers `{meta['providers']}` · model `{meta['model']}`\n\n"
            f"Flags: {json.dumps(meta['flags'])}\n\n"
            f"| Metric | Value |\n|---|---|\n| Runs | {summary['runs']} |\n| Section success rate | {summary['section_success_rate']:.1%} |\n"
            f"| Guides with every section valid | {summary['guides_fully_complete']}/{summary['runs']} |\n"
            f"| Retried sections per guide | {summary['retried_sections_per_guide']} |\n"
            f"| Deferred (on demand) sections per guide | {summary.get('deferred_sections_per_guide', 0)} |\n"
            f"| Time to first chunk p50 / p95 (s) | {f(summary['first_chunk_s']['p50'])} / {f(summary['first_chunk_s']['p95'])} |\n"
            f"| Time to first section p50 / p95 (s) | {f(summary['first_section_s']['p50'])} / {f(summary['first_section_s']['p95'])} |\n"
            f"| Total time p50 / p95 / mean (s) | {f(summary['total_s']['p50'])} / {f(summary['total_s']['p95'])} / {summary['total_s']['mean']} |\n\n"
            f"| Run | Total s | First chunk s | First section s | Sections ok | Retried | Failed | Deferred |\n|---|---|---|---|---|---|---|---|\n{rows}\n")


async def main_async(a) -> dict:
    from app.core.config import get_settings
    st = get_settings()
    runs = []
    for i in range(a.reps):
        r = await one_run(a.topic, a.level, a.score)
        runs.append(r)
        print(f"run {i + 1}/{a.reps}: {r['total_s']:.1f}s, {r['sections_ok']}/{r['sections']} ok, retried={r['retried_sections']}, deferred={r['deferred_sections']}", file=sys.stderr)
    flags = {k: getattr(st, k) for k in ("CONTENT_STREAMING", "CONTENT_NATIVE_SCHEMA", "CONTENT_FEWSHOT", "CONTENT_MERMAID",
                                         "CONTENT_SECTION_CONCURRENCY", "LLM_FIRST_TOKEN_S", "LLM_IDLE_TOKEN_S", "CONTENT_TIME_BUDGET_S", "OLLAMA_NUM_CTX")}
    meta = {"label": a.label, "date": dt.date.today().isoformat(), "topic": a.topic, "level": a.level, "providers": st.LLM_PROVIDERS,
            "model": st.OLLAMA_MODEL if st.LLM_PROVIDERS.split(",")[0] == "ollama" else st.GEMINI_MODEL, "flags": flags}
    return {"meta": meta, "summary": summarise(runs), "runs": runs}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--topic", default="Python decorators")
    ap.add_argument("--level", default="Intermediate")
    ap.add_argument("--score", type=int, default=6)
    ap.add_argument("--label", default="v2 (streaming + schema + few-shot)")
    ap.add_argument("--out", default="../docs/benchmarks")
    a = ap.parse_args()
    res = asyncio.run(main_async(a))
    d = Path(a.out); d.mkdir(parents=True, exist_ok=True)
    model = "".join(c if c.isalnum() or c in ".-" else "-" for c in str(res["meta"]["model"]))   # never overwrite another model's report
    stem = f"live-{res['meta']['date']}-{'v2' if res['meta']['flags']['CONTENT_STREAMING'] else 'v1'}-{model}"
    (d / f"{stem}.json").write_text(json.dumps(res, indent=2, default=str))
    (d / f"{stem}.md").write_text(to_markdown(res["meta"], res["summary"], res["runs"]))
    print(to_markdown(res["meta"], res["summary"], res["runs"]))


if __name__ == "__main__":
    main()
