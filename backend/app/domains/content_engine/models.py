"""Records for the content platform (courses, sections, versions, jobs, events). Plain data, no I/O."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

JOB_STATES = ("NOT_STARTED", "QUEUED", "GENERATING", "VALIDATING", "COMPLETED", "FAILED", "CANCELLED")
ACTIVE_STATES = ("QUEUED", "GENERATING", "VALIDATING")
TERMINAL_STATES = ("COMPLETED", "FAILED", "CANCELLED")


def normalize_topic(text: str) -> str:
    """Canonical form used for identity: NFKC, lower-case, single spaces, surrounding punctuation trimmed."""
    t = unicodedata.normalize("NFKC", text).lower()
    t = re.sub(r"\s+", " ", t).strip(" \t\n\r.,;:!?\"'`")
    return t


@dataclass(frozen=True)
class CourseSpec:
    """The identity of canonical (shared) content. Nothing learner-specific belongs here."""
    topic: str
    level: str = "Intermediate"
    domain: str = ""
    audience: str = "general"
    goal: str = ""
    language: str = "en"

    def normalized(self) -> dict:
        return {"topic": normalize_topic(self.topic), "level": self.level.strip().title(), "domain": normalize_topic(self.domain),
                "audience": normalize_topic(self.audience) or "general", "goal": normalize_topic(self.goal), "language": self.language.strip().lower() or "en"}


@dataclass
class Course:
    course_id: str
    course_key: str
    topic_id: str
    topic: str
    level: str
    domain: str
    audience: str
    goal: str
    language: str
    title: str
    created_by: str | None
    created_at: datetime


@dataclass
class SectionState:
    course_id: str
    section_id: str
    status: str
    current_version: int | None
    updated_at: datetime


@dataclass
class ContentVersion:
    version_id: str
    course_id: str
    section_id: str
    content_version: int
    schema_version: str
    prompt_version: str
    model: str
    provider: str
    status: str
    quality_status: str
    payload: Any
    tokens_in: int
    tokens_out: int
    created_at: datetime
    completed_at: datetime | None
    blocks: list[dict] = field(default_factory=list)


@dataclass
class Job:
    job_id: str
    generation_key: str
    course_id: str
    section_id: str
    status: str
    requested_by: str | None
    force: bool
    attempts: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    error: str | None
    version_id: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@dataclass
class GenerationEvent:
    event_id: int
    job_id: str
    type: str
    data: dict
    created_at: datetime
