"""Lazy, coalescing section generation over the canonical content store.

* One section = one job. `generation_key` (course + section + schema/prompt version) is UNIQUE, so any number of
  concurrent requests for the same section share one job and one model call; the rest wait for its result.
* Content already stored for the current prompt version is returned without a model call (cache-first).
* Usage (provider-reported tokens, latency, outcome) is written to the ledger for every model call.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.domains.content_engine.contracts import LEGACY_ALIASES, SECTION_IDS
from app.domains.content_engine.models import ACTIVE_STATES, ContentVersion, CourseSpec
from app.domains.content_engine.versioning import Versions
from app.infrastructure.database.content_store import ContentStore
from app.infrastructure.database.jobs import JobRepository

log = logging.getLogger("content_service")
SCHEMA_VERSION = "2.0"
V2_TO_LEGACY = {v: k for k, v in LEGACY_ALIASES.items()} | {"learning_roadmap": "learning_roadmap"}


@dataclass
class SectionResult:
    value: Any
    ok: bool
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    model: str = ""
    provider: str = ""


Producer = Callable[[str, CourseSpec], Awaitable[SectionResult]]   # (section_id, spec) -> result


def generation_key(course_id: str, section_id: str, prompt_version: str, schema_version: str = SCHEMA_VERSION) -> str:
    return hashlib.sha256(f"{course_id}|{section_id}|{prompt_version}|{schema_version}".encode()).hexdigest()


class ContentService:
    def __init__(self, store: ContentStore, jobs: JobRepository, producer: Producer, prompt_version: Callable[[str], str],
                 poll_s: float = 0.05, wait_timeout_s: float = 300, lease_s: float = 120):
        self.store, self.jobs, self.producer, self.prompt_version = store, jobs, producer, prompt_version
        self.poll_s, self.wait_timeout_s, self.lease_s = poll_s, wait_timeout_s, lease_s
        self.owner = f"svc-{uuid.uuid4().hex[:8]}"

    def status(self, spec: CourseSpec, user_id: str | None = None) -> dict:
        """Per-section state without triggering any generation."""
        course = self.store.ensure_course(spec, user_id)
        out = []
        for s in self.store.list_sections(course.course_id):
            job = self.jobs.latest_for_section(course.course_id, s.section_id)
            out.append({"section_id": s.section_id, "status": s.status, "version": s.current_version,
                        "job_id": job.job_id if job and job.status in ACTIVE_STATES else None})
        return {"course_id": course.course_id, "sections": out}

    async def get_section(self, spec: CourseSpec, section_id: str, user_id: str | None = None, force: bool = False) -> ContentVersion | None:
        section_id = LEGACY_ALIASES.get(section_id, section_id)
        if section_id not in SECTION_IDS:
            raise KeyError(section_id)
        course = self.store.ensure_course(spec, user_id)
        pv = self.prompt_version(V2_TO_LEGACY.get(section_id, section_id))
        cur = self.store.current_content(course.course_id, section_id)
        if cur and cur.prompt_version == pv and not force:
            return cur                                                   # cache hit: no model call
        key = generation_key(course.course_id, section_id, pv) if not force else generation_key(course.course_id, section_id, pv + f"+force-{uuid.uuid4().hex[:8]}")
        job, created = self.jobs.create_or_get(key, course.course_id, section_id, user_id, force=force)
        if not created and job.status in ("COMPLETED", "FAILED", "CANCELLED"):
            if job.status == "COMPLETED" and not force:
                return self.store.current_content(course.course_id, section_id)
            if self.jobs.requeue_if_terminal(job.job_id):
                created = True                                           # a failed job may be retried by exactly one caller
        if created and self.jobs.claim(job.job_id, self.owner, self.lease_s):
            await self._run(job.job_id, course.course_id, section_id, spec, pv)
        else:
            await self._wait(job.job_id)
        return self.store.current_content(course.course_id, section_id)

    async def _run(self, job_id: str, course_id: str, section_id: str, spec: CourseSpec, pv: str) -> None:
        self.store.set_section_status(course_id, section_id, "GENERATING")
        self.jobs.append_event(job_id, "section.started", {"section_id": section_id})
        try:
            res = await self.producer(V2_TO_LEGACY.get(section_id, section_id), spec)
            self.jobs.record_usage(job_id=job_id, course_id=course_id, section_id=section_id, provider=res.provider, model=res.model,
                                   tokens_in=res.tokens_in, tokens_out=res.tokens_out, latency_ms=res.latency_ms, outcome="ok" if res.ok else "failed")
            if not res.ok:
                raise RuntimeError("generation failed validation")
            cv = self.store.save_version(course_id, section_id, Versions(SCHEMA_VERSION, pv, model=res.model, provider=res.provider),
                                         payload=res.value, tokens_in=res.tokens_in, tokens_out=res.tokens_out)
            self.jobs.set_status(job_id, "COMPLETED", owner=self.owner, version_id=cv.version_id)
            self.jobs.append_event(job_id, "section.completed", {"section_id": section_id, "version": cv.content_version})
        except asyncio.CancelledError:
            self.jobs.set_status(job_id, "CANCELLED", owner=self.owner)
            self.store.set_section_status(course_id, section_id, "NOT_STARTED")
            raise
        except Exception as e:
            self.jobs.set_status(job_id, "FAILED", owner=self.owner, error=type(e).__name__)
            self.store.set_section_status(course_id, section_id, "FAILED")
            self.jobs.append_event(job_id, "section.failed", {"section_id": section_id, "error": type(e).__name__})

    async def _wait(self, job_id: str) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.wait_timeout_s
        while loop.time() < deadline:
            j = self.jobs.get(job_id)
            if j and j.status in ("COMPLETED", "FAILED", "CANCELLED"):
                return
            await asyncio.sleep(self.poll_s)
