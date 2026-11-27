"""Normalise, dedupe, classify, trust-score, verify and rank learning resources."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Awaitable, Callable, Protocol
from urllib.parse import urlsplit

from app.core.exceptions import UnsafeURLError
from app.domains.resource_engine.models import Candidate, Resource, SourceType, TrustStatus
from app.domains.resource_engine.url_safety import validate_url

# curated allow-list of official-documentation hosts (extendable via config)
OFFICIAL_HOSTS = {
    "docs.python.org": "python", "fastapi.tiangolo.com": "fastapi", "www.postgresql.org": "postgresql",
    "docs.docker.com": "docker", "kubernetes.io": "kubernetes", "redis.io": "redis", "langchain-ai.github.io": "langgraph",
    "modelcontextprotocol.io": "mcp", "docs.pydantic.dev": "pydantic", "developer.mozilla.org": "web",
}


class SearchAdapter(Protocol):
    name: str
    async def search(self, query: str, limit: int) -> list[Candidate]: ...


Fetcher = Callable[[str], Awaitable[int]]  # returns HTTP status; must itself be SSRF-safe (no redirect to private IPs)


def canonical_key(url: str) -> str:
    p = urlsplit(url)
    host = p.hostname or ""
    host = host[4:] if host.startswith("www.") else host
    path = re.sub(r"/index\.html?$", "", p.path).rstrip("/")
    return f"{host}{path}".lower()


def classify_source(url: str) -> SourceType:
    host = (urlsplit(url).hostname or "").lower()
    if host in OFFICIAL_HOSTS or host.startswith("docs.") or host.endswith(".readthedocs.io"):
        return SourceType.OFFICIAL_DOCS
    if host in ("github.com", "gitlab.com"):
        return SourceType.GITHUB
    if host.endswith(("coursera.org", "edx.org", "udacity.com", "ocw.mit.edu")):
        return SourceType.COURSE
    if host.endswith(("medium.com", "dev.to", "substack.com", "hashnode.dev")):
        return SourceType.BLOG
    return SourceType.OTHER


_DIFF_HINTS = [(1, ("beginner", "introduction", "getting started", "basics", "tutorial for beginners", "101")),
               (2, ("fundamentals", "quickstart", "quick start", "overview")),
               (4, ("advanced", "deep dive", "internals", "in production", "scaling")),
               (5, ("expert", "research", "paper"))]


def classify_difficulty(title: str, description: str = "") -> int:
    text = f"{title} {description}".lower()
    for level, hints in reversed(_DIFF_HINTS):
        if any(h in text for h in hints):
            return level
    return 2


_BASE_TRUST = {SourceType.OFFICIAL_DOCS: 0.9, SourceType.GITHUB: 0.6, SourceType.COURSE: 0.65, SourceType.TUTORIAL: 0.5,
               SourceType.ARTICLE: 0.45, SourceType.BLOG: 0.35, SourceType.OTHER: 0.3}


def freshness(published_at: datetime | None, last_verified_at: datetime | None, now: datetime,
              half_life_days: float = 730) -> float:
    ref = published_at or last_verified_at
    if ref is None:
        return 0.5  # unknown: neutral, not rewarded
    ref = ref if ref.tzinfo else ref.replace(tzinfo=timezone.utc)
    return 0.5 ** (max(0.0, (now - ref).total_seconds() / 86400) / half_life_days)


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9\+#]+", s.lower()))


def relevance(skill_terms: list[str], r: Resource) -> float:
    q = set().union(*(_tokens(t) for t in skill_terms)) if skill_terms else set()
    if not q:
        return 0.0
    title = _tokens(r.title)
    desc = _tokens(r.description)
    return min(1.0, (len(q & title) * 1.0 + len(q & desc) * 0.4) / len(q))


def score(r: Resource, skill_terms: list[str], learner_level: float | None, now: datetime) -> float:
    trust = _BASE_TRUST[r.source_type]
    if r.trust_status == TrustStatus.VERIFIED:
        trust = min(1.0, trust + 0.1)
    elif r.trust_status in (TrustStatus.UNREACHABLE, TrustStatus.BLOCKED):
        return 0.0
    fit = 1.0 if learner_level is None else math.exp(-abs(r.difficulty - (learner_level + 1)) / 2)  # stretch target
    return round(0.45 * relevance(skill_terms, r) + 0.25 * trust + 0.15 * freshness(r.published_at, r.last_verified_at, now)
                 + 0.15 * fit, 4)


class ResourceService:
    def __init__(self, adapters: list[SearchAdapter] | None = None, fetcher: Fetcher | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.adapters = adapters or []
        self.fetcher = fetcher
        self.clock = clock
        self.warnings: list[str] = []

    def normalise(self, c: Candidate, skill_id: str) -> Resource | None:
        try:
            url = validate_url(c.url)  # SSRF / scheme validation before anything else touches it
        except UnsafeURLError:
            return None
        return Resource(title=re.sub(r"\s+", " ", c.title).strip()[:200], url=url, canonical_key=canonical_key(url),
                        source_type=classify_source(url), skill_id=skill_id,
                        difficulty=classify_difficulty(c.title, c.description), description=c.description[:500],
                        published_at=c.published_at, metadata={"adapter": c.adapter})

    @staticmethod
    def dedupe(resources: list[Resource]) -> list[Resource]:
        best: dict[str, Resource] = {}
        for r in resources:
            cur = best.get(r.canonical_key)
            if cur is None or (r.trust_status == TrustStatus.VERIFIED and cur.trust_status != TrustStatus.VERIFIED):
                best[r.canonical_key] = r
        return list(best.values())

    async def verify(self, r: Resource) -> Resource:
        """Mark VERIFIED only if we actually fetched it and got a 2xx/3xx. Without a fetcher: stays UNVERIFIED."""
        if self.fetcher is None:
            return r
        try:
            status = await self.fetcher(r.url)
        except Exception:
            status = 0
        ok = 200 <= status < 400
        return r.model_copy(update={"trust_status": TrustStatus.VERIFIED if ok else TrustStatus.UNREACHABLE,
                                    "last_verified_at": self.clock()})

    async def discover(self, skill_id: str, skill_terms: list[str], learner_level: float | None = None,
                       limit: int = 10, verify: bool = True) -> list[tuple[Resource, float]]:
        self.warnings = []
        cands: list[Candidate] = []
        for a in self.adapters:
            try:
                cands.extend(await a.search(" ".join(skill_terms), limit * 2))
            except Exception:  # external source down: degrade, don't fail
                self.warnings.append(f"source '{a.name}' unavailable")
        resources = [r for c in cands if (r := self.normalise(c, skill_id))]
        resources = self.dedupe(resources)
        if verify:
            resources = [await self.verify(r) for r in resources]
        now = self.clock()
        ranked = sorted(((r, score(r, skill_terms, learner_level, now)) for r in resources),
                        key=lambda x: (-x[1], x[0].url))
        return [(r, s) for r, s in ranked if s > 0][:limit]
