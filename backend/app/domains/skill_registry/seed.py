"""Seed catalogue (idempotent). (name, category, difficulty, hours, aliases)."""
from __future__ import annotations

from app.core.exceptions import ConflictError
from app.domains.skill_registry.repository import SnapshotSkillRepository
from app.domains.skill_registry.service import SkillRegistry

SKILLS = [
    ("Python", "programming", 1, 40, ["py", "python3", "python programming", "python language"]),
    ("Async Programming", "programming", 3, 15, ["asyncio", "concurrency in python"]),
    ("API Development", "backend", 2, 15, ["web apis", "http apis"]),
    ("FastAPI", "backend", 2, 15, ["fast api"]),
    ("SQL", "data", 1, 25, ["structured query language"]),
    ("PostgreSQL", "data", 2, 20, ["postgres", "psql"]),
    ("Redis", "data", 2, 10, []),
    ("Docker", "devops", 2, 15, ["containers", "containerization"]),
    ("Kubernetes", "devops", 4, 30, ["k8s"]),
    ("Cloud Computing", "devops", 3, 25, ["cloud", "aws/gcp/azure"]),
    ("Distributed Systems", "architecture", 4, 40, []),
    ("System Design", "architecture", 4, 40, ["software architecture"]),
    ("Machine Learning", "ml", 3, 60, ["ml"]),
    ("Deep Learning", "ml", 4, 60, ["dl", "neural networks"]),
    ("LLM Fundamentals", "ai", 3, 20, ["llms", "large language models", "llm"]),
    ("Prompt Engineering", "ai", 2, 10, []),
    ("Tool Calling", "ai", 3, 10, ["function calling"]),
    ("RAG", "ai", 3, 20, ["retrieval augmented generation", "retrieval-augmented generation"]),
    ("MCP", "ai", 3, 10, ["model context protocol"]),
    ("LangGraph", "ai", 3, 15, []),
    ("Agentic AI", "ai", 4, 25, ["ai agents", "agents"]),
    ("Multi-Agent Systems", "ai", 5, 25, ["multi agent"]),
    ("JavaScript", "programming", 1, 40, ["js", "ecmascript"]),
    ("Java", "programming", 2, 50, []),
    ("Data Structures and Algorithms", "programming", 3, 60, ["dsa", "data structures", "algorithms"]),
    ("Object-Oriented Programming", "programming", 2, 20, ["oop", "object oriented programming"]),
    ("REST API Design", "backend", 2, 15, ["rest api", "rest apis", "restful api", "restful apis", "restful api design"]),
    ("Django", "backend", 3, 25, []),
    ("Microservices", "architecture", 4, 30, ["microservice architecture"]),
    ("Generative AI", "ai", 3, 20, ["genai", "gen ai"]),
    ("Kafka", "data", 3, 20, ["apache kafka"]),
    ("Message Queues", "architecture", 2, 10, ["message queue", "queues", "messaging"]),
    ("CI/CD", "devops", 2, 15, ["cicd", "continuous integration", "continuous delivery"]),
    ("Network Security", "security", 3, 30, ["netsec", "network defence"]),
    ("Application Security", "security", 3, 30, ["appsec", "secure coding"]),
    ("Git", "devops", 1, 10, ["version control", "github"]),
    ("Linux", "devops", 2, 20, ["linux command line", "bash", "shell"]),
    ("Networking Fundamentals", "security", 2, 20, ["computer networks", "tcp/ip", "networking"]),
]

# (skill, prerequisite, min_proficiency)
DEPENDENCIES = [
    ("Async Programming", "Python", 3), ("API Development", "Python", 2), ("API Development", "Async Programming", 2),
    ("FastAPI", "API Development", 2), ("FastAPI", "Python", 3), ("Distributed Systems", "FastAPI", 2),
    ("Distributed Systems", "System Design", 2), ("PostgreSQL", "SQL", 2), ("Redis", "Python", 2),
    ("Kubernetes", "Docker", 3), ("Cloud Computing", "Docker", 2), ("System Design", "API Development", 2),
    ("System Design", "PostgreSQL", 2), ("Deep Learning", "Machine Learning", 3), ("Machine Learning", "Python", 3),
    ("LLM Fundamentals", "Machine Learning", 1), ("Prompt Engineering", "LLM Fundamentals", 2),
    ("Tool Calling", "Prompt Engineering", 2), ("MCP", "Tool Calling", 2), ("RAG", "LLM Fundamentals", 2),
    ("RAG", "PostgreSQL", 1), ("Agentic AI", "MCP", 2), ("Agentic AI", "Tool Calling", 2), ("LangGraph", "Agentic AI", 1),
    ("Multi-Agent Systems", "Agentic AI", 3), ("LangGraph", "Python", 3),
    ("Application Security", "API Development", 2), ("Django", "Python", 3), ("REST API Design", "API Development", 2), ("Microservices", "REST API Design", 2), ("Kafka", "Message Queues", 2),
    ("Generative AI", "LLM Fundamentals", 2), ("Data Structures and Algorithms", "Python", 2), ("Distributed Systems", "Message Queues", 1),
    ("Docker", "Linux", 2), ("CI/CD", "Git", 2), ("Network Security", "Networking Fundamentals", 2), ("Networking Fundamentals", "Linux", 1),
    ("Cloud Computing", "Linux", 2), ("Application Security", "Git", 1),
]


def _sync_aliases(registry: SkillRegistry) -> None:
    """The catalogue owns its aliases: databases seeded by an older version get new aliases, and an alias moved between skills (e.g. "rest api" now
    belongs to REST API Design) is moved there too. Idempotent; aliases the catalogue does not mention are left alone."""
    from app.domains.skill_registry.normalize import slugify
    owner = {slugify(a): name for name, _c, _d, _h, aliases in SKILLS for a in aliases}
    for name, _c, _d, _h, aliases in SKILLS:
        s = registry.resolve(name, 1.0)
        if not s:
            continue
        keep = [a for a in s.aliases if owner.get(slugify(a), name) == name]
        want = list(dict.fromkeys(keep + [a for a in aliases if a not in keep]))
        if want != list(s.aliases):
            registry.repo.update(s.model_copy(update={"aliases": want}))


def seed(registry: SkillRegistry) -> None:
    registry = SkillRegistry(SnapshotSkillRepository(registry.repo))   # an already seeded database costs two queries
    for name, cat, diff, hours, aliases in SKILLS:
        try:
            registry.create_skill(name, category=cat, difficulty=diff, estimated_hours=hours, aliases=aliases)
        except ConflictError:
            pass
    _sync_aliases(registry)
    for skill, pre, minp in DEPENDENCIES:
        s, p = registry.resolve(skill, 1.0), registry.resolve(pre, 1.0)
        registry.add_dependency(s.skill_id, p.skill_id, minp)
