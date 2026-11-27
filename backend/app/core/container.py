"""Composition root. Holds process-wide singletons; swap implementations here.

Persistence: SQLAlchemy Core + Alembic (ADR 0006). SQLite by default (DATABASE_PATH, ":memory:" in tests) or PostgreSQL
(STORE_BACKEND=postgres + DATABASE_URL); the same repositories run on both."""
from __future__ import annotations

import threading
from dataclasses import dataclass

from app.domains.assessments.models import Question
from app.domains.assessments.seed import seed_questions
from app.domains.careers.service import CareerService
from app.domains.resource_engine.service import ResourceService
from app.domains.skill_intelligence.explorer import ExplorerService
from app.domains.skill_registry.seed import seed
from app.domains.skill_registry.service import SkillRegistry
from app.infrastructure.cache.cache import Cache, InMemoryBackend
from app.domains.assessments.bank.seeding import seed_question_bank
from app.domains.assessments.bank.service import QuestionBankService
from app.domains.assessments.generation import AssessmentGeneration
from app.infrastructure.database.assessment_store import AssessmentStore
from app.infrastructure.database.content_history_store import ContentHistoryStore
from app.infrastructure.database.learner_store import Attempt, LearnerStore  # noqa: F401 (re-exported)
from app.infrastructure.database.session import get_database, reset_database
from app.infrastructure.database.skill_repository import SqlSkillRepository
from app.infrastructure.database.tutor_store import TutorStore


@dataclass
class Container:
    registry: SkillRegistry
    careers: CareerService
    questions: dict[str, Question]
    store: LearnerStore
    cache: Cache
    resources: ResourceService
    assessments: AssessmentStore | None = None
    bank: QuestionBankService | None = None
    explorer: ExplorerService | None = None
    history: ContentHistoryStore | None = None   # persistent content sessions (the source of truth for generated study material)
    generation: AssessmentGeneration | None = None   # assessment generation lifecycle (detached runner + replayable events)
    tutor: TutorStore | None = None   # tutor conversations beyond the basics in LearnerStore: lifecycle, feedback, tool executions, usage


_container: Container | None = None
_lock = threading.Lock()


def get_container() -> Container:
    """Thread-safe lazy init: sync endpoints run in a thread pool, so the first concurrent requests must not
    build (and seed) the container twice."""
    global _container
    if _container is None:
        with _lock:
            if _container is None:
                db = get_database()
                reg = SkillRegistry(SqlSkillRepository(db))
                seed(reg)
                qs = {q.question_id: q for q in seed_questions(reg)}
                ast_store = AssessmentStore(db)
                seed_question_bank(ast_store, reg)  # idempotent; a single COUNT once seeded
                careers, learner, hist = CareerService(reg), LearnerStore(db), ContentHistoryStore(db)
                _container = Container(reg, careers, qs, learner, Cache(InMemoryBackend()),
                                       ResourceService([]), ast_store, QuestionBankService(ast_store, registry=reg), ExplorerService(reg, careers, learner, hist),
                                       hist)  # no external search adapter configured by default
                _container.generation = AssessmentGeneration(_container.bank, ast_store)
                _container.tutor = TutorStore(db)
    return _container


def reset_container() -> None:
    global _container
    _container = None
    reset_database()
