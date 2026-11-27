"""AI Tutor API: conversations, streaming replies, cancellation, feedback and confirmed actions. Orchestration lives in
`app.domains.tutoring.service`; these handlers do validation, ownership checks and persistence of the learner's question."""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse

from app.ai.runtime import get_router
from app.api.v1.schemas import ConversationCreateIn, ConversationPatchIn, FeedbackIn, RegenerateIn, TutorChatIn
from app.core.config import get_settings
from app.core.container import Container, get_container
from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import rate_limited_user
from app.domains.tutoring import service, tools

router = APIRouter(prefix="/api/v1/tutor", tags=["tutor"])
C = Depends(get_container)
U = Depends(rate_limited_user)
HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


def _conversation(c: Container, user: str, conv_id: str) -> dict:
    conv = c.store.get_conversation(user, conv_id)
    if not conv:
        raise NotFoundError("conversation not found")   # not-yours and missing are indistinguishable
    return conv


def _title(text: str) -> str:
    return " ".join(text.split())[:80] or "New conversation"


def _options(body) -> service.Options:
    return service.Options(skill=body.skill, language=body.language, mode=body.mode, level=body.level, career_goal=body.career_goal)


# ---------------- conversations ----------------
@router.get("/conversations")
def list_conversations(q: str | None = Query(None, max_length=80), user: str = U, c: Container = C):
    return {"items": c.tutor.list_conversations(user, q)}


@router.post("/conversations", status_code=201)
def create_conversation(body: ConversationCreateIn, user: str = U, c: Container = C):
    conv_id = str(uuid4())
    c.store.create_conversation(user, conv_id, _title(body.title or "New conversation"), None, body.language)
    return c.store.get_conversation(user, conv_id)


@router.get("/conversations/{conv_id}")
def get_conversation(conv_id: str, user: str = U, c: Container = C):
    conv = _conversation(c, user, conv_id)
    return {**conv, "messages": service.reconcile(c, conv_id, c.tutor.thread(user, conv_id))}


@router.patch("/conversations/{conv_id}")
def update_conversation(conv_id: str, body: ConversationPatchIn, user: str = U, c: Container = C):
    _conversation(c, user, conv_id)
    c.tutor.update_conversation(user, conv_id, title=_title(body.title) if body.title is not None else None, pinned=body.pinned)
    return c.store.get_conversation(user, conv_id)


@router.delete("/conversations/{conv_id}", status_code=204)
def delete_conversation(conv_id: str, user: str = U, c: Container = C):
    _conversation(c, user, conv_id)
    g = service.generations.active(conv_id)
    if g:
        g.cancel.set()
    c.tutor.delete_conversation(user, conv_id)
    return Response(status_code=204)


# ---------------- replies ----------------
def _stream(c: Container, user: str, generation: service.Generation, question: dict, opts: service.Options) -> StreamingResponse:
    return StreamingResponse(service.run_turn(c, user, generation, question, opts, lambda: get_router()), media_type="text/event-stream", headers=HEADERS)


def _replay(conv_id: str, question: dict, reply: dict) -> StreamingResponse:
    """The same client_message_id was already answered: return the stored reply instead of calling the model twice."""
    async def gen():
        yield service.sse("meta", {"conversation_id": conv_id, "message_id": reply["message_id"], "user_message_id": question["message_id"], "replayed": True})
        for t in reply["tools"]:
            yield service.sse("tool_result", t)
        yield service.sse("token", {"text": reply["content"]})
        yield service.sse("done", {"conversation_id": conv_id, "message_id": reply["message_id"], "status": "complete", "followups": (reply["meta"] or {}).get("followups", [])})
    return StreamingResponse(gen(), media_type="text/event-stream", headers=HEADERS)


@router.post("/chat")
async def chat(body: TutorChatIn, user: str = U, c: Container = C):
    """Server-Sent Events (see `app.domains.tutoring.service`). History is read server-side, never trusted from the client.
    The question is stored before generation. Repeating a `client_message_id` never duplicates it."""
    previous = c.tutor.find_question(user, body.client_message_id) if body.client_message_id else None
    if previous:
        if body.conversation_id and body.conversation_id != previous["conversation_id"]:
            raise ConflictError("this message id belongs to another conversation")
        if previous["content"] != body.message:
            raise ConflictError("this message id was already used for a different message")
        conv_id = previous["conversation_id"]
        thread = c.tutor.thread(user, conv_id)
        later = [m for m in thread if m["message_id"] > previous["message_id"]]
        reply = later[0] if later and later[0]["role"] == "assistant" else None
        if reply and reply["status"] == "complete":
            return _replay(conv_id, previous, reply)
        if any(m["role"] == "user" for m in later):   # answering it again would hide the replies to newer questions
            raise ConflictError("newer messages exist in this conversation; send the question again as a new message")
        generation = service.generations.claim(conv_id)
        c.tutor.supersede_after(conv_id, previous["message_id"])
        return _stream(c, user, generation, previous, _options(body))

    conv_id = body.conversation_id
    if conv_id:
        _conversation(c, user, conv_id)   # also blocks writing into someone else's conversation
    else:
        conv_id = str(uuid4())
        skill = (c.registry.repo.get(body.skill) or c.registry.resolve(body.skill)) if body.skill else None
        c.store.create_conversation(user, conv_id, _title(body.message), skill.skill_id if skill else None, body.language)
    generation = service.generations.claim(conv_id)
    try:
        conv = c.store.get_conversation(user, conv_id)
        if conv["title"] == "New conversation" and not c.tutor.thread(user, conv_id):
            c.tutor.update_conversation(user, conv_id, title=_title(body.message))
        mid = c.store.add_message(user, conv_id, "user", body.message, client_message_id=body.client_message_id)
    except Exception:
        service.generations.release(generation)
        raise
    return _stream(c, user, generation, {"message_id": mid, "content": body.message}, _options(body))


@router.post("/conversations/{conv_id}/regenerate")
async def regenerate(conv_id: str, body: RegenerateIn, user: str = U, c: Container = C):
    """Answer the last question again. The earlier reply is kept in the database but hidden."""
    _conversation(c, user, conv_id)
    question = next((m for m in reversed(c.tutor.thread(user, conv_id)) if m["role"] == "user"), None)
    if not question:
        raise ConflictError("there is no question to answer yet")
    generation = service.generations.claim(conv_id)
    c.tutor.supersede_after(conv_id, question["message_id"])
    return _stream(c, user, generation, question, _options(body))


@router.post("/messages/{message_id}/stop")
async def stop(message_id: int, user: str = U, c: Container = C):
    m = c.tutor.get_message(user, message_id)
    if not m:
        raise NotFoundError("message not found")
    g = service.generations.active(m["conversation_id"])
    stopping = bool(g and g.message_id == message_id)
    if stopping:
        g.cancel.set()
    return {"stopping": stopping}


@router.put("/messages/{message_id}/feedback")
def feedback(message_id: int, body: FeedbackIn, user: str = U, c: Container = C):
    if not c.tutor.set_feedback(user, message_id, {"up": 1, "down": -1, None: None}[body.rating], body.comment):
        raise NotFoundError("reply not found")
    return {"message_id": message_id, "rating": body.rating}


# ---------------- confirmed actions ----------------
@router.post("/actions/{execution_id}/confirm")
async def confirm_action(execution_id: str, user: str = U, c: Container = C):
    """Run an action the tutor proposed, with the stored (server-built) arguments. Safe to repeat: it runs once."""
    ex = c.tutor.get_execution(user, execution_id)
    if not ex:
        raise NotFoundError("action not found")
    if ex["status"] == "succeeded":
        return ex
    if not tools.TOOLS.get(ex["tool"]) or not tools.TOOLS[ex["tool"]].requires_confirmation:
        raise ConflictError("this action cannot be confirmed")
    if not c.tutor.move_execution(user, execution_id, ("pending", "failed"), "running"):
        raise ConflictError("this action is already running or was dismissed")
    status, out = await tools.run_tool(c, user, ex["tool"], ex["args"], key=f"tutor-action-{execution_id}")
    c.tutor.move_execution(user, execution_id, ("running",), status, out)
    return c.tutor.get_execution(user, execution_id)


@router.post("/actions/{execution_id}/dismiss")
def dismiss_action(execution_id: str, user: str = U, c: Container = C):
    if not c.tutor.get_execution(user, execution_id):
        raise NotFoundError("action not found")
    if not c.tutor.move_execution(user, execution_id, ("pending", "failed"), "dismissed"):
        raise ConflictError("this action can no longer be dismissed")
    return c.tutor.get_execution(user, execution_id)


# ---------------- usage ----------------
@router.get("/usage")
def usage(user: str = U, c: Container = C):
    """The learner's own measured tutor figures."""
    s = get_settings()
    out = c.tutor.usage(user)
    priced = s.TUTOR_COST_INPUT_PER_MTOK or s.TUTOR_COST_OUTPUT_PER_MTOK
    out["estimated_cost_usd"] = round((out["input_tokens"] * s.TUTOR_COST_INPUT_PER_MTOK + out["output_tokens"] * s.TUTOR_COST_OUTPUT_PER_MTOK) / 1e6, 6) if priced else None
    return out
