"""Rule-based intent classification for the AI Tutor.

Deterministic on purpose: it adds no model call (so no latency in front of an already slow local model) and its output can never be
steered by retrieved content. Anything the rules do not recognise is an ordinary conversation (`general_knowledge`).

Only the learner's own message is classified; model output is never classified, so an LLM cannot trigger a tool."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

Intent = Literal["general_knowledge", "concept_explanation", "code_generation", "debugging", "comparison", "system_design", "interview_preparation",
                 "learning_plan", "learning_recommendation", "skill_gap_analysis", "progress_review", "assessment_review", "quiz_generation",
                 "content_generation", "revision", "document_qa", "roadmap_creation", "roadmap_review"]
Level = Literal["auto", "beginner", "intermediate", "advanced"]
Mode = Literal["auto", "learn", "code", "practice", "interview", "system_design", "revise"]
Style = Literal["concise", "structured", "step_by_step", "analogy", "practical", "interview"]


class Classification(BaseModel):
    intent: Intent
    topic: str | None = None
    skill_id: str | None = None
    difficulty: Level = "auto"
    response_style: Style = "concise"
    requires_context: bool = False
    requires_retrieval: bool = False
    requires_tool: bool = False
    tool_name: str | None = None
    follow_up: bool = False


class SkillRef(BaseModel):
    skill_id: str
    name: str
    terms: list[str]  # lower-case name, slug and aliases


# programming languages: in "give me a Python implementation" they name the language, not the topic
LANGUAGES = {"python", "javascript", "typescript", "java", "go", "golang", "rust", "c++", "c#", "sql", "bash", "kotlin", "swift"}
# technologies that are worth treating as a topic even when they are not in the skill registry
TECH = ("api|algorithm|async|await|cache|caching|class|closure|compiler|container|database|decorator|docker|endpoint|exception|framework|function|generator|git|"
        "http|index|iterator|kafka|kubernetes|lambda|latency|memcached|microservice|neural|object|orm|pointer|process|protocol|queue|recursion|redis|rest|"
        "server|socket|sql|thread|transaction|transformer|embedding|vector|webhook|graphql|grpc|nginx|spark|airflow|react|vue|angular|django|flask|"
        "machine learning|deep learning|data structure|design pattern|big o|hash|tree|graph|stack|heap|llm|rag|agent|encryption|oauth|jwt|ci/cd|devops")
_TECH = re.compile(rf"\b({TECH})s?\b", re.I)


def _re(p: str) -> re.Pattern:
    return re.compile(p, re.I)


# Ordered: the first matching rule wins.
RULES: list[tuple[Intent, re.Pattern]] = [
    ("document_qa", _re(r"\b(uploaded|attached)\b|\b(this|my|the) (pdf|document|file|attachment|slides?)\b")),
    ("debugging", _re(r"Traceback \(most recent call last\)|^\s*File \".+\", line \d+|\b\w+(Error|Exception)\b\s*:|\b(debug|stack ?trace)\b|"
                      r"\b(fix|solve) (this|my|the) (error|bug|code|exception|issue)\b|\b(my|this) (code|script|query|function|app|test)\b.{0,60}\b(not working|fails?|failing|broken|crash\w*|throws?|error)\b|"
                      r"\bwhy (am i getting|do i get)\b.{0,40}\b(error|exception)\b|\b(python|javascript|java|sql) exception\b")),
    ("skill_gap_analysis", _re(r"\bskill[- ]?gaps?\b|\bgaps? in my (skills|knowledge)\b|\b(what|which) skills\b.{0,40}\b(missing|lack(ing)?|need)\b|\bam i missing\b|\bmy weak(est)? (skill|area|topic)s?\b|\banaly[sz]e my skills\b")),
    ("assessment_review", _re(r"\b(my|the) (last |latest |previous |recent |past )?(assessment|quiz|test|diagnostic)( study)?\s*(result|score|performance|attempt|mistake)s?\b|"
                              r"\b(explain|review|analy[sz]e|go over)\b.{0,30}\bmy (last |latest |previous |recent )?(assessment|quiz|test|diagnostic)\b|\bhow did i (do|perform|score)\b|\bwhat did i get wrong\b")),
    ("progress_review", _re(r"\b(evaluate|review|assess|check|show|how is|how's|summari[sz]e)\b.{0,25}\bmy (learning )?progress\b|\bhow am i (doing|progressing)\b")),
    ("revision", _re(r"\b(revise|revision|recap)\b|\bsummari[sz]e\b.{0,40}\b(i|we|my)\b.{0,20}\b(learn(ed|t|ing)?|studied|study|material|content|session)\b|\bwhat (did|have) (i|we) (study|studied|learn|learned|learnt|cover|covered)\b")),
    ("content_generation", _re(r"\b(generate|create|make|build|prepare)\b.{0,40}\b(study (guide|material|notes)|lesson|flash ?cards|course material)\b")),
    ("quiz_generation", _re(r"\bquiz(zes)?\b|\btest (my knowledge|me)\b|\b(mcqs?|practice questions|multiple[- ]choice questions)\b")),
    ("roadmap_creation", _re(r"\b(create|generate|make|build|prepare|design|give me)\b.{0,50}\broad ?map\b")),
    ("roadmap_review", _re(r"\b(my|current) road ?map\b|\bnext milestone\b|\broad ?map progress\b")),
    ("learning_plan", _re(r"\b\d+[- ](day|week|month)s?\b.{0,40}\b(plan|schedule)\b|\b(study|learning|preparation) (plan|schedule)\b|\bplan (to|for) learn\w*\b|\b(plan|schedule)\b.{0,20}\b\d+[- ](day|week|month)s?\b")),
    ("learning_recommendation", _re(r"\bwhat (should|can|do) i (learn|study|do) next\b|\bwhat('s| is) next for me\b|\brecommend\b.{0,40}\b(skill|topic|course|resource)s?\b")),
    ("interview_preparation", _re(r"\binterview\w*\b")),
    ("system_design", _re(r"\bsystem design\b|\b(hld|lld)\b|\b(design|architect)\b.{0,50}\b(system|shortener|service|platform|architecture|backend|news ?feed|chat app|rate limiter|pipeline|application)\b")),
    ("comparison", _re(r"\bcompare\b|\bcomparison\b|\bdifferences? between\b|\b(vs\.?|versus)\b")),
    ("code_generation", _re(r"\b(write|create|generate|implement|build|give|show|code|refactor|optimi[sz]e|add)\b.{0,60}\b(code|function|method|class|script|program|endpoint|query|queries|implementation|snippet|unit tests?|tests for|decorator|dockerfile|regex)\b|"
                            r"\b(implementation|code example|sample code)\b|```")),
    ("concept_explanation", _re(r"\b(explain|teach me|describe|walk me through|help me understand)\b|^\s*(what is|what's|what are|how does|how do|how is|why is|why does|why do)\b")),
]

MODE_DEFAULT: dict[str, Intent] = {"learn": "concept_explanation", "code": "code_generation", "practice": "quiz_generation", "interview": "interview_preparation",
                                   "system_design": "system_design", "revise": "revision"}
STRUCTURED: set[str] = {"concept_explanation", "comparison", "system_design", "learning_plan", "skill_gap_analysis", "progress_review", "assessment_review", "revision"}
# intents whose answer should be grounded in the learner's own data
LEARNER_INTENTS: set[str] = {"learning_plan", "learning_recommendation", "skill_gap_analysis", "progress_review", "assessment_review", "quiz_generation",
                             "revision", "roadmap_creation", "roadmap_review", "interview_preparation", "content_generation"}
TOOL_FOR: dict[str, str] = {"skill_gap_analysis": "analyze_skill_gaps", "learning_recommendation": "analyze_skill_gaps", "progress_review": "get_progress",
                            "assessment_review": "get_assessment_results", "roadmap_review": "get_roadmap", "roadmap_creation": "generate_roadmap",
                            "quiz_generation": "create_quiz", "content_generation": "open_study_material", "revision": "get_learning_history"}

_FOLLOW = _re(r"\b(it|its|it's|this|that|these|those|they|them|the same|above|previous answer|continue)\b|^\s*(and|also|but|so|then|what about|how about|now|more|why\??$|an? example|give (me )?(an? )?(example|more))\b")
_CHATTY_QUIZ = _re(r"\bone (question )?at a time\b|\bquiz me\b|\bask me\b")
_TOPIC_PHRASE = _re(r"^\s*(?:please\s+)?(?:can you\s+)?(?:explain|teach me|describe|what is|what's|what are|how does|how do|walk me through|help me understand|compare)\s+(?:an?\s+|the\s+|about\s+)?(.{2,60}?)(?:\s+(?:work|works|internally|like|to me|with|in simple|in detail|step by step)\b.*)?[?.!]*\s*$")


def find_skill(message: str, skills: list[SkillRef], *, skip_languages: bool = False) -> SkillRef | None:
    """The registry skill named in the message (longest term wins, so "machine learning" beats "learning")."""
    low = message.lower()
    best: tuple[int, int, SkillRef] | None = None
    for s in skills:
        for term in s.terms:
            if len(term) < 2 or (skip_languages and term in LANGUAGES):
                continue
            m = re.search(rf"(?<![\w+#]){re.escape(term)}(?![\w+#])", low)
            if m and (best is None or (len(term), -m.start()) > (best[0], best[1])):
                best = (len(term), -m.start(), s)
    return best[2] if best else None


def _topic_of(message: str) -> str | None:
    m = _TOPIC_PHRASE.match(message)
    if m:
        t = m.group(1).strip(" ?.!,")
        # pronouns refer back to the conversation, and "my ..." refers to the learner's own records: neither names a topic
        if t and not re.fullmatch(r"(it|this|that|they|them|these|those|my|me|our)(\b.*)?", t, re.I):
            return t
    m = _TECH.search(message)
    return m.group(0) if m else None


def _style(message: str, intent: str, mode: str) -> Style:
    low = message.lower()
    if intent == "interview_preparation" or mode == "interview":
        return "interview"
    if re.search(r"step[- ]by[- ]step", low):
        return "step_by_step"
    if "analogy" in low or "like i'm" in low or "eli5" in low:
        return "analogy"
    if re.search(r"\b(practical|hands[- ]on|real[- ]world)\b", low):
        return "practical"
    if re.search(r"\b(briefly|in short|one[- ]line|quick(ly)? answer|tl;?dr)\b", low):
        return "concise"
    return "structured" if intent in STRUCTURED else "concise"


def _difficulty(message: str, level: str) -> Level:
    if level != "auto":
        return level  # type: ignore[return-value]
    low = message.lower()
    if re.search(r"\b(beginner|i'?m new|new to|eli5|like i'?m (five|5)|simple terms|from scratch)\b", low):
        return "beginner"
    if re.search(r"\b(advanced|in[- ]depth|deep dive|expert)\b", low):
        return "advanced"
    if "intermediate" in low:
        return "intermediate"
    return "auto"


def classify(message: str, skills: list[SkillRef], *, mode: str = "auto", level: str = "auto", focus: SkillRef | None = None,
             previous: Classification | None = None) -> Classification:
    text = message.strip()
    intent: Intent | None = next((name for name, rx in RULES if rx.search(text)), None)
    matched = intent is not None

    code_like = intent in ("code_generation", "debugging")
    named = find_skill(text, skills, skip_languages=code_like)
    if named is None and intent == "debugging":   # "debug a Python exception": the language is the subject
        named = find_skill(text, skills)
    phrase = None if code_like else _topic_of(text)
    refers_back = bool(previous and previous.topic and len(text) < 200 and _FOLLOW.search(text))
    topic, skill_id, follow_up = None, None, False
    if named:
        topic, skill_id = named.name, named.skill_id
    elif phrase and not refers_back:
        topic = phrase
    elif _TECH.search(text) and not refers_back:
        topic = _TECH.search(text).group(0)
    # follow-up: no topic of its own and the conversation already has one
    if topic is None and previous and previous.topic and len(text) < 200 and (refers_back or intent in (None, "code_generation", "comparison", "quiz_generation", "interview_preparation")):
        topic, skill_id, follow_up = previous.topic, previous.skill_id, True
    elif refers_back:
        follow_up = True
    if topic is None and code_like:
        lang = find_skill(text, skills)
        if lang:
            topic, skill_id = lang.name, lang.skill_id
    if topic is None and focus and (intent in LEARNER_INTENTS or intent in STRUCTURED or follow_up):
        topic, skill_id = focus.name, focus.skill_id

    if intent is None:
        if mode in MODE_DEFAULT:
            intent = MODE_DEFAULT[mode]
        elif previous and previous.intent == "interview_preparation":       # an answer inside a running mock interview
            intent, follow_up = "interview_preparation", True
            topic, skill_id = topic or previous.topic, skill_id or previous.skill_id
        elif follow_up and previous:
            intent = previous.intent if previous.intent in ("concept_explanation", "code_generation", "comparison", "system_design", "debugging") else "concept_explanation"
        else:
            intent = "general_knowledge"
    # "what is the capital of France" is not a lesson: only technical or registry topics get the teaching format
    elif intent == "concept_explanation" and not (skill_id or follow_up or _TECH.search(text) or re.search(r"\b(explain|teach me|help me understand)\b", text, re.I)):
        intent = "general_knowledge"

    tool = TOOL_FOR.get(intent)
    if intent == "quiz_generation" and (_CHATTY_QUIZ.search(text) or (mode == "practice" and not matched) or not skill_id):
        tool = None  # a conversational quiz (or a topic with no question bank) is answered in chat
    if intent == "learning_recommendation" and re.search(r"\bresources?\b", text, re.I):
        tool = None
    return Classification(intent=intent, topic=topic, skill_id=skill_id, difficulty=_difficulty(text, level), response_style=_style(text, intent, mode),
                          requires_context=intent in LEARNER_INTENTS or bool(skill_id), requires_retrieval=intent in ("revision", "document_qa"),
                          requires_tool=tool is not None, tool_name=tool, follow_up=follow_up)
