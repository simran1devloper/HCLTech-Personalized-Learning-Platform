"""
services/quiz_pipeline.py — quiz generation as a bounded, observable agentic pipeline.

    plan  ->  generate (LLM)  ->  review (deterministic validation)  --issues-->  generate (one revision)  ->  sign

The orchestration is the LangGraph ContentWorkflow (bounded revisions, per-node timeout, cancellation).
Business rules (what makes a quiz acceptable) are plain code in the reviewer, not LLM judgement.
Every step is reported as a stage event so the UI shows real checkpoints.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Callable, Dict, Optional

from app.agents.workflows.content_workflow import Cancellation, ContentWorkflow
from app.core.config import get_settings
from services import gemini_service as gs
from services.pipeline import Tracker, stage_list

log = logging.getLogger("quiz_pipeline")

MIN_VALID = 5
STAGES = stage_list(("plan", "Plan the assessment"), ("generate", "Generate questions"),
                    ("review", "Validate questions"), ("sign", "Sign quiz for secure grading"))
LABEL = {s["id"]: s["label"] for s in STAGES}


class _QueueSink:
    def __init__(self, q): self.q = q
    async def emit(self, run_id, event, data): pass  # stage events are emitted by the nodes themselves (richer detail)


async def run_quiz_pipeline(topic: str, sign: Callable[[list], str],
                            generate_raw: Optional[Callable[[str], Any]] = None) -> AsyncIterator[Dict[str, Any]]:
    """Yields {'event': 'meta'|'stage'|'done'|'error', 'data': ...}. Closing the iterator cancels the workflow
    (and the in-flight model request)."""
    st = get_settings()
    q: asyncio.Queue = asyncio.Queue()
    t = Tracker(q)
    gen = generate_raw or (lambda prompt: gs._generate(prompt, temperature=0.6, response_mime_type="application/json"))
    attempts = {"n": 0}
    yield {"event": "meta", "data": {"stages": STAGES, "workflow": "quiz-v1", "max_revisions": 1}}

    async def planner(req: dict) -> dict:
        t.emit("plan", LABEL["plan"], "running")
        plan = {"topic": req["topic"], "n_questions": 10, "types": "conceptual, scenario, analytical, edge-case"}
        t.emit("plan", LABEL["plan"], "done", detail=f"10 questions on “{plan['topic']}” (conceptual → scenario → analytical → edge-case)")
        return plan

    async def generator(plan: dict, prev: Any, issues: list) -> Any:
        attempts["n"] += 1
        n = attempts["n"]
        t.emit("generate", LABEL["generate"], "retrying" if n > 1 else "running", attempt=n, max_attempts=2,
               detail=("Revising after review feedback: " + "; ".join(issues)) if issues else "Asking the model for 10 questions…")
        try:
            raw = await gen(gs.build_quiz_prompt(plan["topic"], "; ".join(issues)))
        except Exception as e:
            t.emit("generate", LABEL["generate"], "failed", attempt=n, detail="The AI service did not respond.")
            log.warning("quiz generation call failed: %s", type(e).__name__)
            raise
        questions, n_raw = gs.parse_quiz(raw, plan["topic"])
        t.emit("generate", LABEL["generate"], "done", attempt=n, detail=f"{n_raw} questions returned, {len(questions)} usable")
        return questions

    async def reviewer(draft: list) -> list:
        t.emit("review", LABEL["review"], "running", detail=f"Checking {len(draft)} questions: 4 options each, answer key present…")
        issues = [f"only {len(draft)} valid questions (need at least {MIN_VALID})"] if len(draft) < MIN_VALID else []
        if issues:
            t.emit("review", LABEL["review"], "failed", detail=issues[0] + (" — retrying generation" if attempts["n"] < 2 else ""))
        else:
            t.emit("review", LABEL["review"], "done", detail=f"{len(draft)} questions accepted")
        return issues

    wf = ContentWorkflow(planner, generator, reviewer, max_revisions=1, node_timeout_s=st.LLM_TIMEOUT_S + 30,
                         sink=_QueueSink(q), cancel=Cancellation())

    async def run():
        try:
            return await wf.run({"topic": topic, "run_id": "quiz"})
        finally:
            q.put_nowait(None)

    task = asyncio.create_task(run())
    try:
        while (item := await q.get()) is not None:
            yield item
        final = task.result()
        questions = final.get("draft") or []
        if final.get("status") != "completed" or len(questions) < MIN_VALID:
            msg = ("The model could not produce a usable quiz. Please try again."
                   if final.get("status") in ("needs_human_review", "completed") else "The AI service is unavailable. Please try again.")
            yield {"event": "error", "data": {"message": msg, "stage": "generate" if final.get("status") == "failed" else "review"}}
            return
        t.emit("sign", LABEL["sign"], "running")
        token = sign(questions)
        t.emit("sign", LABEL["sign"], "done", detail="Answer key stays on the server; grading uses a signed copy")
        while not q.empty():
            item = q.get_nowait()
            if item:
                yield item
        yield {"event": "done", "data": {"quiz": {"topic": topic, "questions": questions, "quiz_token": token}}}
    finally:
        if not task.done():  # client disconnected: stop the workflow and the model request
            wf.cancel.cancel()
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
