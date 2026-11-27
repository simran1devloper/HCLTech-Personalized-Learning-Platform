"""
services/gemini_service.py — Google Gemini API integration
All prompt design and AI generation logic lives here.
"""

import re
from typing import Any, Dict, List, Optional

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.ai.langchain_adapter import RouterChatModel
from app.ai.model_router import Complexity
from app.core.config import get_settings
from utils.helpers import extract_json_from_text

# Provider-independent: Ollama (local) and/or Gemini (cloud), chosen by LLM_PROVIDERS. Built lazily so importing
# this module never needs credentials or a running model server.
from app.ai.runtime import get_router  # shared router (single set of circuit breakers)


def _chat_model(temperature: float, json_mode: bool = False):
    """LangChain chat model over the shared ModelRouter (configured provider order, retries, circuit breakers, failover).
    JSON requests need a structured-output-capable provider; with LLM_LOCAL_ONLY=true nothing falls back to the cloud."""
    s = get_settings()
    return RouterChatModel(router_getter=lambda: get_router(), temperature=temperature, max_output_tokens=s.LLM_MAX_OUTPUT_TOKENS,
                           timeout_s=s.LLM_TIMEOUT_S, json_mode=json_mode, complexity=Complexity.MEDIUM)


async def _generate(prompt: str, temperature: float = 0.7, response_mime_type: Optional[str] = None) -> str:
    """One LCEL call: ChatPromptTemplate | RouterChatModel | StrOutputParser."""
    chain = ChatPromptTemplate.from_messages([("human", "{prompt}")]) | _chat_model(temperature, response_mime_type == "application/json") | StrOutputParser()
    return await chain.ainvoke({"prompt": prompt})


# ══════════════════════════════════════════════
# QUIZ GENERATION
# ══════════════════════════════════════════════

QUIZ_PROMPT = """You are an expert educational assessment designer specializing in adaptive diagnostics.

Generate exactly 10 multiple-choice quiz questions to assess a student's knowledge about: "{topic}"

QUESTION TYPE DISTRIBUTION (strictly follow this):
  - Questions 1–3:  CONCEPTUAL — Test understanding of definitions, principles, and core theory.
                    Example format: "Which of the following BEST describes X?" or "What is the primary purpose of Y?"
  - Questions 4–6:  SCENARIO-BASED — Present a real-world situation and ask what should be done.
                    Example format: "A developer notices X happening. What is the MOST LIKELY cause?" or "Given scenario Y, which approach is correct?"
  - Questions 7–8:  ANALYTICAL — Require comparing, contrasting, or evaluating trade-offs.
                    Example format: "What is the key difference between X and Y?" or "Which implementation is more efficient and why?"
  - Questions 9–10: ADVANCED / EDGE-CASE — Test deep knowledge, gotchas, or subtle nuances.
                    Example format: "What happens when X occurs in situation Y?" or "Which statement about edge case Z is TRUE?"

STRICT REQUIREMENTS:
- Each question must test a DISTINCT concept — no repetition
- Each question must have exactly 4 answer options
- The correct_answer field must be the EXACT TEXT of the correct option (not A/B/C/D)
- Distractors must be plausible but clearly wrong to experts; avoid trick wording
- Scenario questions must describe a concrete, realistic situation (2–3 sentences minimum)
- All questions must be unambiguous and accurate

Return ONLY a valid JSON object with no extra text, markdown, or explanation:

{{
  "questions": [
    {{
      "question": "Question text here?",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "correct_answer": "Option A"
    }}
  ]
}}

Topic: {topic}
Generate exactly 10 questions now."""


def build_quiz_prompt(topic: str, feedback: str = "") -> str:
    prompt = QUIZ_PROMPT.format(topic=topic)
    if feedback:  # reviewer feedback from a previous attempt
        prompt += f"\n\nIMPORTANT — the previous attempt was rejected: {feedback} Return exactly 10 valid questions, each with 4 options."
    return prompt


def parse_quiz(raw: str, topic: str = "") -> tuple[List[Dict[str, Any]], int]:
    """Returns (valid_questions, n_raw_questions). Never raises on unusable output: returns ([], 0) instead."""
    parsed = extract_json_from_text(raw)
    if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
        parsed = parsed[0]
    if not isinstance(parsed, dict) or not isinstance(parsed.get("questions"), list):
        return [], 0
    questions = [q for q in parsed["questions"] if isinstance(q, dict)]
    validated = []
    for q in questions[:10]:
        if not isinstance(q.get("options"), list) or len(q["options"]) < 4:
            continue
        if not q.get("question") or not q.get("correct_answer"):
            continue
        if q["correct_answer"] not in q["options"]:  # ensure the key is one of the options
            q["correct_answer"] = q["options"][0]
        validated.append({"question": str(q["question"]), "options": [str(o) for o in q["options"][:4]],
                          "correct_answer": str(q["correct_answer"])})
    return validated, len(questions)


async def generate_quiz(topic: str) -> List[Dict[str, Any]]:
    """Generate up to 10 MCQ questions for a topic (single attempt). Raises ValueError if too few are valid."""
    raw = await _generate(build_quiz_prompt(topic), temperature=0.6, response_mime_type="application/json")
    validated, _ = parse_quiz(raw, topic)
    if len(validated) < 5:
        raise ValueError("The model returned too few valid questions. Please try again.")
    return validated


# ══════════════════════════════════════════════
# CONTENT GENERATION — see services/content_generation.py (generated section by section)
# ══════════════════════════════════════════════


# ══════════════════════════════════════════════
# AGENT CHAT
# ══════════════════════════════════════════════

AGENT_SYSTEM_PROMPT = """You are LearnMate, an elite AI learning coach and subject-matter expert.
Your personality is warm, encouraging, intellectually sharp, and deeply student-focused.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STUDENT CONTEXT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • Current topic:        {topic}
  • Proficiency level:    {level}
  • Quiz score:           {score}/10

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR TEACHING APPROACH
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. CALIBRATE to the student's level:
       - Beginner:     Use simple language, analogies, step-by-step breakdowns. Never assume prior knowledge.
       - Intermediate: Balance clarity with depth. Introduce technical terms with brief explanations.
       - Expert:       Engage as a peer. Use precise terminology, explore nuance, discuss trade-offs.

  2. STRUCTURE every response for scannability:
       - Lead with the direct answer in 1–2 sentences.
       - Use **bold** for key terms, `inline code` for syntax, bullet points for lists.
       - Add a short code block if it clarifies a concept faster than prose.
       - Close with ONE actionable next step or follow-up question to deepen engagement.

  3. DIAGNOSE gaps proactively:
       - If the student's question reveals a misconception, gently correct it first.
       - Connect new questions back to {topic} fundamentals where relevant.
       - If a question is outside {topic}, briefly answer but redirect focus.

  4. KEEP responses focused and concise (3–5 short paragraphs or equivalent bullet structure).
     Never write walls of text. Quality > quantity.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TONE GUIDELINES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✅ DO:  Be encouraging, precise, intellectually curious.
  ✅ DO:  Celebrate progress ("Great question!", "You're thinking exactly right here!")
  ✅ DO:  Use real-world analogies and concrete examples.
  ❌ DON'T: Be robotic, overly formal, or condescending.
  ❌ DON'T: Give vague non-answers or hedge excessively.
  ❌ DON'T: Write more than necessary — brevity is a virtue.

Respond now as LearnMate."""


async def chat_with_agent(
    topic: str,
    level: str,
    message: str,
    history: List[Dict[str, str]],
    score: int = 0
) -> str:
    """
    Generate a contextual, personalized AI tutor response.

    Args:
        topic:   Current learning topic.
        level:   Student's proficiency level.
        message: Latest user message.
        history: List of {role, content} message history.
        score:   Student's quiz score (default 0 for backward-compat).

    Returns:
        AI tutor response string.
    """
    prompt = ChatPromptTemplate.from_messages([("system", AGENT_SYSTEM_PROMPT), MessagesPlaceholder("history"), ("human", "{message}")])
    chain = prompt | _chat_model(0.75) | StrOutputParser()
    response = await chain.ainvoke({
        "topic": topic, "level": level, "score": score, "message": message,
        "history": [("human" if m["role"] == "user" else "ai", m["content"]) for m in history[-8:]]})  # last 8: context window

    # Clean up any role prefixes the model might add
    response = re.sub(
        r'^(LearnMate:|Tutor:|Personalized Learning AI:|Assistant:)\s*',
        '',
        response.strip()
    )

    return response