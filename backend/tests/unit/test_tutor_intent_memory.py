"""AI Tutor: intent classification, conversation memory, prompts and provider message history (no model, no database)."""
import json

import httpx
import pytest

from app.ai.model_router import ModelRouter
from app.ai.providers.gemini import GeminiProvider
from app.ai.providers.ollama import OllamaProvider
from app.ai.providers.openai_compat import OpenAICompatProvider
from app.ai.types import GenerationParams
from app.core.exceptions import ProviderAccountError, ProviderError
from app.domains.tutoring import prompts
from app.domains.tutoring.intent import Classification, SkillRef, classify
from app.domains.tutoring.memory import build_memory, shorten, tokens


def ref(sid, name, *aliases):
    return SkillRef(skill_id=sid, name=name, terms=[name.lower(), *aliases])


SKILLS = [ref("py", "Python"), ref("fa", "FastAPI", "fast api"), ref("rd", "Redis"), ref("sd", "System Design")]


@pytest.mark.parametrize("message,intent,topic,tool", [
    ("Explain Redis", "concept_explanation", "Redis", None),
    ("Compare Redis and Memcached", "comparison", "Redis", None),
    ("Write a FastAPI endpoint", "code_generation", "FastAPI", None),
    ("Debug a Python exception", "debugging", "Python", None),
    ("Design a URL shortener", "system_design", None, None),
    ("Create a Kafka learning plan", "learning_plan", "Kafka", None),
    ("Generate a FastAPI quiz", "quiz_generation", "FastAPI", "create_quiz"),
    ("Analyze my skill gaps", "skill_gap_analysis", None, "analyze_skill_gaps"),
    ("Explain my previous assessment", "assessment_review", None, "get_assessment_results"),
    ("Summarize uploaded learning material", "document_qa", None, None),
])
def test_required_intent_scenarios(message, intent, topic, tool):
    c = classify(message, SKILLS)
    assert (c.intent, c.tool_name) == (intent, tool)
    if topic:
        assert c.topic.lower() == topic.lower()
    assert c.requires_tool == (tool is not None)


@pytest.mark.parametrize("message,intent", [
    ("What is the capital of France?", "general_knowledge"), ("hi", "general_knowledge"), ("How do I ask for a raise?", "general_knowledge"),
    ("What should I learn next?", "learning_recommendation"), ("Which skills am I missing for an AI Engineer role?", "skill_gap_analysis"),
    ("Create a 30-day learning plan", "learning_plan"), ("Create a roadmap for Data Engineer", "roadmap_creation"), ("Show my roadmap", "roadmap_review"),
    ("Evaluate my learning progress", "progress_review"), ("Help me prepare for technical interviews", "interview_preparation"),
    ("TypeError: 'NoneType' object is not subscriptable", "debugging"), ("Help me revise what I studied", "revision"), ("Solve x^2 = 9", "general_knowledge"),
])
def test_other_requests(message, intent):
    assert classify(message, SKILLS).intent == intent


def test_follow_ups_keep_the_topic_of_the_conversation():
    first = classify("Explain Redis.", SKILLS)
    internals = classify("How does it work internally?", SKILLS, previous=first)
    assert internals.topic == "Redis" and internals.follow_up and internals.intent == "concept_explanation"
    code = classify("Give me a Python implementation.", SKILLS, previous=internals)
    assert code.topic == "Redis" and code.intent == "code_generation"   # "Python" names the language, not a new topic
    new = classify("Explain Kafka", SKILLS, previous=code)
    assert new.topic == "Kafka" and not new.follow_up
    unrelated = classify("What is the capital of France?", SKILLS, previous=first)
    assert unrelated.intent == "general_knowledge" and unrelated.topic != "Redis"


def test_chat_quizzes_unknown_topics_modes_and_interviews():
    assert classify("Quiz me on Redis, one question at a time", SKILLS).tool_name is None
    assert classify("Generate a quiz about medieval history", SKILLS).tool_name is None   # no question bank: answered in chat
    assert classify("Generate a React quiz", SKILLS, focus=SKILLS[0]).tool_name is None  # the focus skill never replaces a named topic
    assert classify("Redis", SKILLS, mode="interview").intent == "interview_preparation"
    started = classify("Interview me on Python", SKILLS)
    answer = classify("A list is mutable and a tuple is not", SKILLS, previous=started)
    assert answer.intent == "interview_preparation" and answer.topic == "Python"


def test_requests_about_the_learners_own_records_have_no_invented_topic():
    for message in ("Explain my previous assessment briefly", "Explain my weakest skill", "Help me understand my results"):
        assert classify(message, SKILLS).topic is None, message


def test_difficulty_and_style():
    assert classify("Explain Redis like I'm new to it", SKILLS).difficulty == "beginner"
    assert classify("Explain Redis", SKILLS, level="advanced").difficulty == "advanced"
    assert classify("Explain Redis step by step", SKILLS).response_style == "step_by_step"
    assert classify("Explain Redis", SKILLS).difficulty == "auto"


def test_prompts_are_versioned_intent_specific_and_bounded():
    code = classify("Write a FastAPI endpoint", SKILLS)
    text = prompts.system_prompt(code, "hinglish")
    assert prompts.PROMPT_VERSION and "Hinglish" in text and "edge cases" in text and "Never say code was executed" in text
    assert tokens(text) < 500   # leaves room for history in a 4k context
    assert prompts.max_output_tokens(classify("hi", SKILLS), 1600) <= 700 < prompts.max_output_tokens(classify("Design a chat system", SKILLS), 1600)


# ---------------- memory ----------------
def msg(role, content, status="complete"):
    return {"role": role, "content": content, "status": status}


def test_a_long_answer_never_erases_the_conversation():
    """Regression: the old trimming stopped at the first over-budget turn, so one long answer removed all history."""
    history = [msg("user", "Explain Redis."), msg("assistant", "Redis is an in-memory data store. " * 600)]
    recent, _ = build_memory(history, 600)
    assert [m["role"] for m in recent] == ["user", "assistant"] and "Redis" in recent[0]["content"] and "shortened" in recent[1]["content"]
    assert sum(tokens(m["content"]) for m in recent) <= 600


def test_older_turns_are_summarised_and_budget_respected():
    history = []
    for i in range(20):
        history += [msg("user", f"question {i} about topic{i}"), msg("assistant", f"answer {i} " * 40)]
    recent, summary = build_memory(history, 500)
    assert recent[0]["role"] == "user" and "answer 19" in recent[-1]["content"] and "topic0" in summary
    assert sum(tokens(m["content"]) for m in recent) + tokens(summary) <= 600


def test_memory_skips_hidden_and_empty_replies_and_merges_repeated_questions():
    history = [msg("user", "first"), msg("assistant", "", "failed"), msg("user", "second"), msg("assistant", "old", "superseded"), msg("assistant", "kept", "cancelled")]
    recent, _ = build_memory(history, 500)
    assert recent == [{"role": "user", "content": "first\n\nsecond"}, {"role": "assistant", "content": "kept"}]
    assert shorten("abc", 100) == "abc" and build_memory([], 100) == ([], "")


# ---------------- providers ----------------
HISTORY = (("user", "Explain Redis."), ("assistant", "A data store."))


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def collect(provider, params=None):
    return [t async for t in provider.generate_stream("How?", params or GenerationParams(system="S", history=HISTORY))]


async def test_providers_send_history_as_real_turns():
    seen = {}

    def ollama(r):
        seen["ollama"] = json.loads(r.content)
        return httpx.Response(200, text=json.dumps({"message": {"content": "ok"}, "done": True}))

    def openai(r):
        seen["openai"] = json.loads(r.content)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":2}}\n\ndata: [DONE]\n\n')

    def gemini(r):
        seen["gemini"] = json.loads(r.content)
        return httpx.Response(200, text='data: {"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}\n\n')
    ends = []
    assert await collect(OllamaProvider("m", client=client(ollama))) == ["ok"]
    assert await collect(OpenAICompatProvider("m", client=client(openai)), GenerationParams(system="S", history=HISTORY, on_stream_end=ends.append)) == ["ok"]
    assert await collect(GeminiProvider("m", "k", client=client(gemini))) == ["ok"]
    assert [m["role"] for m in seen["ollama"]["messages"]] == ["system", "user", "assistant", "user"] == [m["role"] for m in seen["openai"]["messages"]]
    assert [m["role"] for m in seen["gemini"]["contents"]] == ["user", "model", "user"]
    assert (ends[-1].finish_reason, ends[-1].prompt_tokens, ends[-1].completion_tokens) == ("stop", 7, 2)


async def test_invalid_gemini_key_is_an_account_error_so_the_router_fails_over():
    def bad_key(_):
        return httpx.Response(400, json={"error": {"message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}})

    def good(_):
        return httpx.Response(200, text=json.dumps({"message": {"content": "fallback"}, "done": True}))
    gemini = GeminiProvider("m", "bad", client=client(bad_key))
    with pytest.raises(ProviderAccountError):
        await collect(gemini)
    router = ModelRouter([gemini, OllamaProvider("m", client=client(good))])
    assert [t async for t in router.generate_stream("hi")] == ["fallback"]


@pytest.mark.parametrize("status,body", [(401, "{}"), (429, "{}"), (503, "{}")])
async def test_provider_http_failures_surface_as_provider_errors(status, body):
    p = OpenAICompatProvider("m", client=client(lambda _: httpx.Response(status, text=body)))
    with pytest.raises(ProviderError):
        await collect(p)


async def test_malformed_stream_lines_are_skipped_and_midstream_errors_raised():
    garbled = 'data: not json\n\ndata: {"unexpected":1}\n\ndata: {"choices":[{"delta":{"content":"fine"}}]}\n\ndata: [DONE]\n\n'
    assert await collect(OpenAICompatProvider("m", client=client(lambda _: httpx.Response(200, text=garbled)))) == ["fine"]
    broken = 'data: {"choices":[{"delta":{"content":"a"}}]}\n\ndata: {"error":{"message":"overloaded"}}\n\n'
    with pytest.raises(ProviderError):
        await collect(OpenAICompatProvider("m", client=client(lambda _: httpx.Response(200, text=broken))))
    assert isinstance(Classification(intent="general_knowledge"), Classification)
