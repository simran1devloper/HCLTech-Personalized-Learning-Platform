"""Authored starter question bank (provenance=authored). LLM-generated questions are added via the generator
and carry provenance 'llm:<provider>/<model>' and must pass Question validation before entering the bank."""
from __future__ import annotations

from app.domains.assessments.models import Question, QuestionType as T, RubricCriterion


def seed_questions(registry) -> list[Question]:
    sid = lambda n: registry.resolve(n, 1.0).skill_id
    mc = lambda i, skill, d, prompt, opts, ok, why, obj: Question(
        question_id=i, skill_id=sid(skill), type=T.MCQ, prompt=prompt, difficulty=d, options=opts, correct=[ok],
        explanation=why, objective=obj)
    return [
        mc("py-1", "Python", 1, "What does `len([1, 2, 3])` return?", ["2", "3", "4", "Error"], 1, "len counts items.", "built-ins"),
        mc("py-2", "Python", 2, "Which type is immutable?", ["list", "dict", "tuple", "set"], 2, "Tuples cannot be modified after creation.", "data types"),
        mc("py-3", "Python", 3, "What does `@functools.lru_cache` do?", ["Logs calls", "Memoizes results by arguments", "Runs code in a thread", "Freezes a class"], 1, "It caches return values keyed on arguments.", "decorators"),
        mc("py-4", "Python", 4, "Why can a mutable default argument (`def f(x=[])`) cause bugs?", ["It is evaluated once at definition time and shared", "It is evaluated per call", "It raises SyntaxError", "It is copied on write"], 0, "Defaults are evaluated once, so state leaks across calls.", "function semantics"),
        Question(question_id="py-5", skill_id=sid("Python"), type=T.MULTI, prompt="Select all valid ways to create a dict.", difficulty=2,
                 options=["{'a': 1}", "dict(a=1)", "dict([('a', 1)])", "[('a', 1)]"], correct=[0, 1, 2], explanation="A list of tuples is not itself a dict.", objective="data types"),
        Question(question_id="py-6", skill_id=sid("Python"), type=T.CODING, prompt="Write `solve(nums)` returning the sum of even numbers.", difficulty=2,
                 test_cases=[{"input": [[1, 2, 3, 4]], "output": 6}, {"input": [[]], "output": 0}], objective="loops and conditionals"),
        mc("sql-1", "SQL", 1, "Which clause filters rows before grouping?", ["HAVING", "WHERE", "ORDER BY", "LIMIT"], 1, "WHERE filters rows; HAVING filters groups.", "filtering"),
        mc("sql-2", "SQL", 3, "Which join returns all left rows, with NULLs where there is no match?", ["INNER", "LEFT", "CROSS", "SELF"], 1, "LEFT JOIN keeps unmatched left rows.", "joins"),
        mc("fa-1", "FastAPI", 2, "How does FastAPI validate request bodies?", ["Manually in handlers", "Via Pydantic models", "Via the database", "It does not"], 1, "Request bodies are validated through Pydantic models.", "validation"),
        Question(question_id="sd-1", skill_id=sid("System Design"), type=T.SYSTEM_DESIGN, prompt="Design a URL shortener that handles read-heavy traffic.", difficulty=4,
                 rubric=[RubricCriterion(id="c", description="caching for hot reads", keywords_any=["cache", "redis", "cdn"], weight=2),
                         RubricCriterion(id="k", description="unique key generation", keywords_any=["base62", "hash", "counter", "snowflake"]),
                         RubricCriterion(id="s", description="storage / scaling strategy", keywords_any=["shard", "partition", "replica"])],
                 explanation="Strong answers address caching, key generation and storage scaling.", objective="scalability trade-offs"),
    ]
