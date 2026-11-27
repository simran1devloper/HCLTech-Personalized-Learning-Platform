from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    OFFICIAL_DOCS = "official_docs"
    GITHUB = "github"
    COURSE = "course"
    TUTORIAL = "tutorial"
    ARTICLE = "article"
    BLOG = "blog"
    OTHER = "other"


class TrustStatus(str, Enum):
    UNVERIFIED = "unverified"  # never fetched
    VERIFIED = "verified"  # fetched successfully by us at last_verified_at
    UNREACHABLE = "unreachable"  # fetch attempted and failed
    BLOCKED = "blocked"


class Resource(BaseModel):
    resource_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    url: str
    canonical_key: str  # for duplicate detection
    source_type: SourceType = SourceType.OTHER
    skill_id: str
    difficulty: int = Field(2, ge=1, le=5)
    description: str = ""
    trust_status: TrustStatus = TrustStatus.UNVERIFIED
    last_verified_at: datetime | None = None
    published_at: datetime | None = None
    metadata: dict = {}


class Candidate(BaseModel):
    """Raw result from a source adapter, before validation."""
    title: str
    url: str
    description: str = ""
    published_at: datetime | None = None
    adapter: str = ""
