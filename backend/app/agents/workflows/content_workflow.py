"""Bounded plan -> generate -> review(-> revise) workflow on LangGraph.

Deterministic business logic (validation, review checks) lives in injected callables, not in agents.
Guarantees: max_revisions bound, per-node timeout, cooperative cancellation, checkpoint events per node.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, TypedDict

from langgraph.graph import END, StateGraph

from app.core.exceptions import AppError


class WorkflowCancelled(AppError):
    status_code, code = 409, "workflow_cancelled"


class WorkflowState(TypedDict, total=False):
    request: dict
    plan: dict
    draft: Any
    issues: list[str]
    revisions: int
    status: str  # running | completed | failed | cancelled | needs_human_review
    error: str


class EventSink(Protocol):
    async def emit(self, run_id: str, event: str, data: dict) -> None: ...


class ListSink:
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    async def emit(self, run_id, event, data):
        self.events.append((run_id, event, data))


@dataclass
class Cancellation:
    cancelled: bool = False

    def cancel(self):
        self.cancelled = True


@dataclass
class ContentWorkflow:
    planner: Callable[[dict], Awaitable[dict]]
    generator: Callable[[dict, Any, list[str]], Awaitable[Any]]  # (plan, previous_draft, issues) -> draft
    reviewer: Callable[[Any], Awaitable[list[str]]]  # returns issues; [] means approved
    max_revisions: int = 2
    node_timeout_s: float = 120.0
    sink: EventSink = field(default_factory=ListSink)
    cancel: Cancellation = field(default_factory=Cancellation)

    def _guard(self, name: str, fn):
        async def node(state: WorkflowState) -> WorkflowState:
            if self.cancel.cancelled:
                await self.sink.emit(state["request"].get("run_id", ""), "cancelled", {"node": name})
                return {"status": "cancelled"}
            if state.get("status") in ("failed", "cancelled"):
                return {}
            run_id = state["request"].get("run_id", "")
            await self.sink.emit(run_id, f"{name}.started", {})
            try:
                out = await asyncio.wait_for(fn(state), self.node_timeout_s)
            except asyncio.TimeoutError:
                await self.sink.emit(run_id, f"{name}.timeout", {})
                return {"status": "failed", "error": f"{name} timed out"}
            except Exception as e:  # recorded; message is generic, details stay in logs
                await self.sink.emit(run_id, f"{name}.failed", {"type": type(e).__name__})
                return {"status": "failed", "error": f"{name} failed"}
            await self.sink.emit(run_id, f"{name}.completed", {k: out[k] for k in ("status", "issues", "revisions") if k in out})
            return out
        return node

    def build(self):
        async def plan(s):
            return {"plan": await self.planner(s["request"]), "revisions": 0}

        async def generate(s):
            return {"draft": await self.generator(s["plan"], s.get("draft"), s.get("issues", []))}

        async def review(s):
            issues = await self.reviewer(s["draft"])
            if not issues:
                return {"issues": [], "status": "completed"}
            rev = s.get("revisions", 0) + 1
            if rev > self.max_revisions:  # bounded: stop and surface, don't loop forever
                return {"issues": issues, "revisions": rev, "status": "needs_human_review"}
            return {"issues": issues, "revisions": rev}

        g = StateGraph(WorkflowState)
        g.add_node("plan", self._guard("plan", plan))
        g.add_node("generate", self._guard("generate", generate))
        g.add_node("review", self._guard("review", review))
        g.set_entry_point("plan")
        g.add_edge("plan", "generate")
        g.add_edge("generate", "review")
        g.add_conditional_edges("review", lambda s: END if s.get("status") in ("completed", "failed", "cancelled", "needs_human_review") else "generate")
        return g.compile()

    async def run(self, request: dict) -> WorkflowState:
        graph = self.build()
        final = await graph.ainvoke({"request": request, "status": "running", "revisions": 0},
                                    config={"recursion_limit": 6 + 3 * (self.max_revisions + 1)})
        await self.sink.emit(request.get("run_id", ""), "workflow." + final.get("status", "unknown"), {})
        return final
