from __future__ import annotations

import json
from typing import Any, Dict


def _f(x: Any, nd: int = 1) -> str:
    return "–" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def to_markdown(r: Dict[str, Any]) -> str:
    m, s = r["meta"], r["scenarios"]
    c = s["cold_generation"]["summary"]
    rep, con, rst, par = s["repeat_request"], s["concurrent_duplicates"], s["restart_survival"], s["partial_failure"]
    out = [f"# Benchmark report — {m['label']}", "",
           f"*Generated {m['date']} · mode `{m['mode']}` · Python {m['python']}*", "",
           "## Environment", "", "```json", json.dumps(m["model"], indent=2), "```", "",
           f"App settings: section concurrency = {m['app']['CONTENT_SECTION_CONCURRENCY']}, "
           f"sections = {m['app']['sections']}, pipeline flag = `{m['app']['CONTENT_PIPELINE']}`.", ""]
    if m["mode"] == "fake":
        out += ["> **Simulated model.** The real section-generation code runs against a deterministic fake model (latency = "
                "TTFT + tokens / throughput, scaled; reported in *simulated seconds*). Token counts are exact for output "
                "(the section budget × fill) and estimated for input (`len(prompt)//4`). This measures *our system's* "
                "behaviour, not a model's speed. A live baseline must be recorded separately.", ""]
    out += ["## 1. Cold generation of a full study guide", "",
            "| Metric | min | P50 | P95 | max |", "|---|---:|---:|---:|---:|"]
    for key, label in (("time_to_first_section_s", "Time to first section (s)"), ("section_completion_s", "Section completion time (s)"),
                       ("total_s", "Total generation time (s)")):
        v = c[key]
        out.append(f"| {label} | {_f(v['min'])} | {_f(v['p50'])} | {_f(v['p95'])} | {_f(v['max'])} |")
    out += ["", f"Model calls: **{c['model_calls']}** · input tokens: **{c['tokens_in']}** · output tokens: **{c['tokens_out']}** "
            f"· usage counters match the model ledger: **{c['usage_matches_ledger']}**.",
            "*Time to first block:* not measurable at baseline — content is generated per section, not per block (Iteration 3).", "",
            "## 2. Identical repeat request (same process)", "",
            f"Served from cache: **{rep['served_from_cache']}** · model calls: **{rep['repeat_model_calls']}** "
            f"(cold run used {rep['cold_calls']}) · wall time {_f(rep['repeat_wall_ms'])} ms.", "",
            f"## 3. {con['simultaneous_identical_requests']} simultaneous identical requests", "",
            f"Unique sections needed: **{con['unique_sections_needed']}** · model calls made: **{con['model_calls']}** · "
            f"**duplicate calls: {con['duplicate_calls']}** · wasted token share: **{_f(con['wasted_token_share'] * 100)}%** · "
            f"total time {_f(con['total_s'])} s.", "",
            "## 4. Survival across an API restart", "",
            f"Calls before restart: {rst['calls_before_restart']} · calls for the same request after restart: "
            f"**{rst['calls_after_restart']}** · completed content survives restart: **{rst['completed_content_survives_restart']}**.", "",
            "## 5. One section fails, then is regenerated", "",
            f"First run: {par['first_run_calls']} calls, incomplete = {par['first_run_incomplete']} · regeneration used "
            f"**{par['regeneration_calls']}** call(s); only the failed section regenerated: "
            f"**{par['regeneration_only_failed_section']}**.", ""]
    return "\n".join(out)
