"""Learner analytics computed ONLY from stored evidence/attempts/roadmaps. Metrics without a data source are
returned as null with an explicit reason; nothing is estimated or invented."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone


def _d(dt: datetime) -> date:
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).date()


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def build_summary(*, attempts: list[dict], snapshots: list[dict], estimates: dict, roadmaps: list[dict],
                  skill_names: dict[str, str], now: datetime, weeks: int = 12) -> dict:
    """attempts: [{completed_at: datetime, score: float, n_questions: int, skill_id}] (completed only).
    snapshots: rows of proficiency_snapshots. estimates: skill_id -> ProficiencyEstimate.
    roadmaps: [{roadmap_id, goal, version, done, total}]."""
    today = _d(now)
    active_days = sorted({_d(a["completed_at"]) for a in attempts})

    # weekly activity (last N weeks, zero-filled so charts have a stable axis)
    start = _week_start(today) - timedelta(weeks=weeks - 1)
    wk: dict[date, dict] = {start + timedelta(weeks=i): {"assessments": 0, "questions": 0} for i in range(weeks)}
    for a in attempts:
        w = _week_start(_d(a["completed_at"]))
        if w in wk:
            wk[w]["assessments"] += 1
            wk[w]["questions"] += a["n_questions"]

    # streak: consecutive active days ending today or yesterday
    streak, cursor, s = 0, today, set(active_days)
    if cursor not in s:
        cursor -= timedelta(days=1)
    while cursor in s:
        streak += 1
        cursor -= timedelta(days=1)
    last28 = {d for d in active_days if (today - d).days < 28}

    # proficiency series per skill
    series: dict[str, list[dict]] = defaultdict(list)
    for r in snapshots:
        series[r["skill_id"]].append({"at": r["snapshot_at"], "score": r["proficiency_score"], "confidence": r["confidence_score"]})

    retention = []
    for sid, pts in series.items():
        if len(pts) >= 2:
            a0, a1 = datetime.fromisoformat(pts[0]["at"]), datetime.fromisoformat(pts[-1]["at"])
            retention.append({"skill_id": sid, "skill": skill_names.get(sid, sid), "first": pts[0]["score"], "last": pts[-1]["score"],
                              "delta": round(pts[-1]["score"] - pts[0]["score"], 3), "days_between": (a1 - a0).days})

    ranked = sorted(((e.proficiency_score, e.confidence_score, sid) for sid, e in estimates.items() if e.evidence_count),
                    key=lambda x: (-x[0], x[2]))
    row = lambda t: {"skill_id": t[2], "skill": skill_names.get(t[2], t[2]), "proficiency": t[0], "confidence": t[1]}

    total = sum(r["total"] for r in roadmaps)
    done = sum(r["done"] for r in roadmaps)
    return {
        "skills_assessed": sum(1 for e in estimates.values() if e.evidence_count),
        "assessments_completed": len(attempts),
        "weekly_activity": [{"week_start": w.isoformat(), **v} for w, v in sorted(wk.items())],
        "accuracy_trend": [{"at": a["completed_at"].isoformat(), "score": a["score"], "skill_id": a["skill_id"],
                            "skill": skill_names.get(a["skill_id"], a["skill_id"])} for a in sorted(attempts, key=lambda x: x["completed_at"])][-200:],
        "proficiency_series": [{"skill_id": sid, "skill": skill_names.get(sid, sid), "points": pts[-100:]} for sid, pts in series.items()],
        "consistency": {"active_days_last_28": len(last28), "ratio": round(len(last28) / 28, 3),
                        "current_streak_days": streak, "basis": "days with at least one completed assessment"},
        "roadmap_completion": {"done": done, "total": total, "ratio": round(done / total, 3) if total else None,
                               "roadmaps": roadmaps},
        "strongest": [row(t) for t in ranked[:5]],
        "weakest": [row(t) for t in sorted(ranked, key=lambda x: (x[0], x[2]))[:5]],
        "retention": retention,
        "learning_hours": None,
        "unavailable": {"learning_hours": "Study time is not tracked by the backend yet.",
                        "retention": "Shown only for skills assessed at least twice; this is score change between assessments, "
                                     "not a forgetting-curve model."},
    }


def build_insights(summary: dict, gap: dict | None) -> list[dict]:
    """Rule-based observations. Each cites the figure it is based on."""
    out = []
    c = summary["consistency"]
    if summary["assessments_completed"] == 0:
        out.append({"kind": "start", "text": "No assessments completed yet. Take one to establish your first skill evidence."})
    else:
        if c["current_streak_days"] >= 3:
            out.append({"kind": "streak", "text": f"{c['current_streak_days']}-day assessment streak.", "evidence": c})
        if c["active_days_last_28"] <= 3:
            out.append({"kind": "consistency", "text": f"Active on {c['active_days_last_28']} of the last 28 days; short, regular sessions improve retention.", "evidence": c})
    for r in summary["retention"]:
        if r["delta"] <= -0.5:
            out.append({"kind": "regression", "text": f"{r['skill']} dropped {abs(r['delta']):.1f} points between assessments; consider a review.", "evidence": r})
    low = [s for s in summary["weakest"] if s["confidence"] < 0.4]
    if low:
        out.append({"kind": "low_confidence", "text": f"Low evidence confidence for {', '.join(s['skill'] for s in low[:3])}; another assessment would firm up the estimate.", "evidence": low[:3]})
    if gap and gap.get("recommended_next_name"):
        out.append({"kind": "next", "text": f"Highest-priority next skill: {gap['recommended_next_name']}.", "evidence": gap.get("top")})
    return out
