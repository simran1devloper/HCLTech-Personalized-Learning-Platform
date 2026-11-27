"""Run the baseline benchmark.

    cd backend && python -m benchmarks.run_baseline --mode fake --reps 5 --out ../docs/benchmarks

`--mode fake` (default) uses the deterministic simulated model. `--mode live` calls the configured real provider
(wall-clock, no scaling) and runs only the cold and repeat scenarios; it was NOT run in the Iteration 0 baseline
because the hosted model was unreachable at the time.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import platform
import sys
from pathlib import Path

from benchmarks import scenarios as sc
from benchmarks.fake_model import FakeModel
from benchmarks.report import to_markdown
from services import content_generation as cg


async def run(mode: str, reps: int, model: FakeModel, label: str) -> dict:
    from app.core.config import get_settings
    st = get_settings()
    out = {"meta": {"label": label, "date": dt.date.today().isoformat(), "mode": mode, "python": platform.python_version(),
                    "model": ({"kind": "fake", "throughput_tps": model.throughput_tps, "ttft_s": model.ttft_s,
                               "server_slots": model.server_slots, "time_scale": model.time_scale, "fill": model.fill}
                              if mode == "fake" else {"kind": "live", "providers": st.LLM_PROVIDERS, "model": st.OLLAMA_MODEL}),
                    "app": {"CONTENT_SECTION_CONCURRENCY": st.CONTENT_SECTION_CONCURRENCY, "sections": len(cg.SECTION_NAMES),
                            "CONTENT_PIPELINE": st.CONTENT_PIPELINE}},
           "scenarios": {}}
    if mode == "fake":
        original = cg._call_model
        cg._call_model = model
        try:
            out["scenarios"]["cold_generation"] = await sc.cold_generation(model, reps)
            out["scenarios"]["repeat_request"] = await sc.repeat_request(model)
            out["scenarios"]["concurrent_duplicates"] = await sc.concurrent_duplicates(model)
            out["scenarios"]["restart_survival"] = await sc.restart_survival(model)
            out["scenarios"]["partial_failure"] = await sc.partial_failure(model)
        finally:
            cg._call_model = original
    else:
        raise SystemExit("live mode is intentionally not part of the recorded baseline; run it manually against a reachable provider")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["fake", "live"], default="fake")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--time-scale", type=float, default=0.005)
    ap.add_argument("--tps", type=float, default=7.0)
    ap.add_argument("--ttft", type=float, default=2.0)
    ap.add_argument("--slots", type=int, default=2)
    ap.add_argument("--label", default="baseline (pre-v2 content pipeline)")
    ap.add_argument("--out", default="../docs/benchmarks")
    a = ap.parse_args()
    model = FakeModel(throughput_tps=a.tps, ttft_s=a.ttft, server_slots=a.slots, time_scale=a.time_scale)
    result = asyncio.run(run(a.mode, a.reps, model, a.label))
    d = Path(a.out); d.mkdir(parents=True, exist_ok=True)
    stem = f"baseline-{a.mode}-{result['meta']['date']}"
    (d / f"{stem}.json").write_text(json.dumps(result, indent=2, default=str))
    (d / f"{stem}.md").write_text(to_markdown(result))
    print(f"wrote {d / stem}.json and .md", file=sys.stderr)
    print(to_markdown(result))


if __name__ == "__main__":
    main()
