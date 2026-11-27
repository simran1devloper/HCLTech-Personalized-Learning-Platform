"""
routes/learning.py — Learning endpoints
POST /learning/generate-quiz
POST /learning/submit-quiz
POST /learning/generate-content
GET  /learning/history
POST /learning/agent-chat
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from schemas.learning_schema import (
    QuizGenerateRequest, QuizGenerateResponse,
    QuizSubmitRequest, QuizSubmitResponse,
    ContentGenerateRequest, ContentGenerateResponse, ContentStreamRequest,
    HistoryResponse, HistoryItem,
    AgentChatRequest, AgentChatResponse
)
from services.quiz_service import generate_quiz, evaluate_quiz
from services.quiz_pipeline import run_quiz_pipeline
from services.content_service import generate_content, get_history, _record_history
from services import personalization as pz
from services import session_generation as sg
from services.pipeline import with_heartbeats
from app.core.exceptions import NotFoundError
from app.core.container import get_container
from services.agent_service import chat as agent_chat
from utils.jwt_handler import decode_access_token
from utils.helpers import sanitize_topic
from config import settings

log = logging.getLogger("learning")
router = APIRouter()

AI_UNAVAILABLE = "The AI service is temporarily unavailable. Please try again."


def _sign_quiz(user_id: str, topic: str, questions: list) -> str:
    """Server-signed quiz so the grader never has to trust client-supplied correct answers."""
    return jwt.encode({"sub": user_id, "typ": "quiz", "topic": topic, "qs": questions,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=2)},
                      settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def _verify_quiz(token: str, user_id: str) -> dict:
    try:
        data = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired quiz token.")
    if data.get("typ") != "quiz" or data.get("sub") != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Quiz token does not belong to this user.")
    return data
security = HTTPBearer()


# ── Auth Dependency ──

def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """Extract and validate user_id from Bearer token."""
    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload."
        )
    return user_id


# ══════════════════════════════════════════════
# POST /learning/generate-quiz
# ══════════════════════════════════════════════

@router.post(
    "/generate-quiz",
    response_model=QuizGenerateResponse,
    summary="Generate a diagnostic quiz for a topic"
)
async def generate_quiz_endpoint(
    payload: QuizGenerateRequest,
    user_id: str = Depends(get_current_user_id)
):
    """
    Generate 10 MCQ questions for the given topic using Gemini AI.

    Requires: Bearer token
    """
    topic = sanitize_topic(payload.topic)

    try:
        result = await generate_quiz(topic)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e)
        )
    except Exception:
        log.exception("quiz generation failed")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=AI_UNAVAILABLE)

    result["quiz_token"] = _sign_quiz(user_id, topic, result["questions"])
    return result


# ══════════════════════════════════════════════
# POST /learning/generate-quiz/stream  (SSE: agentic pipeline progress)
# ══════════════════════════════════════════════

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/generate-quiz/stream", summary="Generate a quiz and stream the pipeline's progress")
async def generate_quiz_stream_endpoint(payload: QuizGenerateRequest, user_id: str = Depends(get_current_user_id)):
    """
    Server-Sent Events: `meta` {stages} → `stage` {id,label,status,attempt?,duration_ms?,detail?}* → `done` {quiz} | `error`.
    The final `quiz` has the same shape as POST /generate-quiz (including quiz_token).
    """
    topic = sanitize_topic(payload.topic)

    async def events():
        try:
            async for ev in run_quiz_pipeline(topic, sign=lambda qs: _sign_quiz(user_id, topic, qs)):
                yield _sse(ev["event"], ev["data"])
        except Exception:
            log.exception("quiz stream failed")
            yield _sse("error", {"message": AI_UNAVAILABLE})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ══════════════════════════════════════════════
# POST /learning/submit-quiz
# ══════════════════════════════════════════════

@router.post(
    "/submit-quiz",
    response_model=QuizSubmitResponse,
    summary="Submit quiz answers for evaluation"
)
async def submit_quiz_endpoint(
    payload: QuizSubmitRequest,
    user_id: str = Depends(get_current_user_id)
):
    """
    Evaluate quiz answers and return score, level, and per-question breakdown.

    Level classification:
    - 0–4  → Beginner
    - 5–7  → Intermediate
    - 8–10 → Expert

    Requires: Bearer token
    """
    questions = payload.questions
    if payload.quiz_token:
        # Authoritative path: questions + correct answers come from the signed server copy.
        questions = _verify_quiz(payload.quiz_token, user_id)["qs"]
    elif not settings.ALLOW_UNSIGNED_QUIZ:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="quiz_token is required.")
    else:
        log.warning("submit-quiz without quiz_token: grading client-supplied answers (legacy, forgeable)")

    if not questions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Questions list cannot be empty."
        )

    topic = sanitize_topic(payload.topic)

    result = evaluate_quiz(
        topic=topic,
        answers=payload.answers,
        questions=questions
    )

    return result


# ══════════════════════════════════════════════
# POST /learning/generate-content
# ══════════════════════════════════════════════

@router.post(
    "/generate-content",
    response_model=ContentGenerateResponse,
    summary="Generate personalized study materials"
)
async def generate_content_endpoint(
    payload: ContentGenerateRequest,
    user_id: str = Depends(get_current_user_id)
):
    """
    Generate full personalized study materials including:
    - Summary
    - Key concepts
    - Detailed explanation
    - Flashcards
    - Recommendations
    - AI agent intro message

    Also persists the learning session to history.

    Requires: Bearer token
    """
    topic = sanitize_topic(payload.topic)

    try:
        content = await generate_content(
            topic=topic,
            level=payload.level,
            score=payload.score,
            user_id=user_id
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e)
        )
    except Exception:
        log.exception("content generation failed")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=AI_UNAVAILABLE)

    try:  # saved into the same session model; a persistence problem must not lose the content the user just waited for
        await asyncio.to_thread(sg.persist_complete_content, get_container().history, user_id, topic, payload.level, payload.score, content,
                                content.get("incomplete_sections", []))
    except Exception:
        log.exception("could not save generated content to history")

    return {
        "topic":                    topic,
        "level":                    payload.level,
        "summary":                  content.get("summary", ""),
        "key_concepts":             content.get("key_concepts", []),
        "deep_dive":                content.get("deep_dive", ""),
        "real_world_applications":  content.get("real_world_applications", ""),
        "viva_voice":               content.get("viva_voice", ""),
        # Practical Implementation Guide replaces "Best practices"; the old field is kept (same text) for existing clients.
        "practical_guide":          content.get("practical_guide", ""),
        "best_practices":           content.get("practical_guide", ""),
        "learning_roadmap":         content.get("learning_roadmap", ""),
        "flashcards":               content.get("flashcards", []),
        "recommendations":          content.get("recommendations", {}),
        "agent_intro":              content.get("agent_intro", ""),
        "incomplete_sections":      content.get("incomplete_sections", []),
    }


# ══════════════════════════════════════════════
# POST /learning/generate-content/stream  (SSE)
# ══════════════════════════════════════════════

@router.post("/generate-content/stream", summary="Generate study materials, streaming each section as it completes")
async def generate_content_stream_endpoint(
    payload: ContentStreamRequest,
    user_id: str = Depends(get_current_user_id)
):
    """
    Server-Sent Events: `meta` {sections,total,cached} → `section` {name,value,ok}* → `done` {content,incomplete_sections}
    (or `error`). Pass `sections` to regenerate only those (e.g. ones that failed). If the client disconnects,
    generation stops and in-flight model requests are cancelled.
    """
    topic = sanitize_topic(payload.topic)
    store = get_container().history
    # Every generation belongs to a persistent, user-owned session (created here, or the existing one for this topic+level). Resolved before
    # streaming starts so a foreign/unknown session_id is a clean 404, not a broken stream.
    try:
        session, created = await asyncio.to_thread(sg.open_session, store, user_id, topic, payload.level, payload.score,
                                                   session_id=payload.session_id, new_session=payload.new_session)
    except NotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content session not found.")

    # Personalisation comes from stored data only (career goal, the learner's own diagnostic results) and costs no model call.
    profile = await asyncio.to_thread(pz.build_profile, get_container(), user_id, payload.level, payload.diagnostic_session_id)
    await asyncio.to_thread(store.update_context, user_id, session["id"], {"profile": pz.public_summary(profile)})

    async def events():
        sent = 0
        try:
            async for ev in with_heartbeats(sg.stream_session(store, user_id, session, only=payload.sections, created=created, profile=profile)):
                if ev is None:  # idle: SSE comment keeps proxies (nginx/ngrok) from closing the connection
                    yield ": ping\n\n"
                    continue
                if ev["event"] == "section" and ev["data"]["ok"]:
                    sent += 1
                if ev["event"] == "done" and sent and not payload.sections:  # partial regeneration never added a history row
                    await _record_history(user_id, topic, payload.score, payload.level)  # legacy learning_history row (dashboard "previous sessions")
                yield _sse(ev["event"], ev["data"])
        except Exception:
            log.exception("content stream failed")
            yield _sse("error", {"message": AI_UNAVAILABLE, "partial": sent > 0})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ══════════════════════════════════════════════
# GET /learning/history
# ══════════════════════════════════════════════

@router.get(
    "/history",
    response_model=HistoryResponse,
    summary="Get user's learning history"
)
async def get_history_endpoint(
    user_id: str = Depends(get_current_user_id)
):
    """
    Retrieve the authenticated user's past learning sessions.

    Returns up to 20 most recent sessions, sorted newest first.

    Requires: Bearer token
    """
    try:
        history = await asyncio.to_thread(get_history, user_id)  # sync Supabase client: keep off the event loop
    except Exception:
        log.exception("history fetch failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch history.")

    return {
        "history": [
            HistoryItem(
                id=str(item["id"]),
                topic=item["topic"],
                score=item["score"],
                level=item["level"],
                created_at=item["created_at"]
            )
            for item in history
        ]
    }


# ══════════════════════════════════════════════
# POST /learning/agent-chat
# ══════════════════════════════════════════════

@router.post(
    "/agent-chat",
    response_model=AgentChatResponse,
    summary="Chat with the AI tutor agent"
)
async def agent_chat_endpoint(
    payload: AgentChatRequest,
    user_id: str = Depends(get_current_user_id)
):
    """
    Send a message to the proactive AI tutor agent.

    The agent is aware of:
    - The current topic
    - The student's proficiency level
    - The conversation history

    Requires: Bearer token
    """
    try:
        reply = await agent_chat(
            topic=payload.topic,
            level=payload.level,
            message=payload.message,
            history=[{"role": m.role, "content": m.content} for m in payload.history]
        )
    except Exception:
        log.exception("agent chat failed")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=AI_UNAVAILABLE)

    return AgentChatResponse(reply=reply)
