from datetime import datetime, timedelta, timezone
import pytest
from app.domains.resource_engine.models import Candidate, TrustStatus, SourceType
from app.domains.resource_engine.service import (ResourceService, canonical_key, classify_difficulty, classify_source,
                                                 freshness, score)

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


class Adapter:
    def __init__(self, name, results=None, fail=False): self.name, self.results, self.fail = name, results or [], fail
    async def search(self, q, limit):
        if self.fail: raise RuntimeError("down")
        return self.results


def C(title, url, desc="", pub=None): return Candidate(title=title, url=url, description=desc, published_at=pub, adapter="t")


def test_canonical_and_classification():
    assert canonical_key("https://www.Example.com/a/index.html") == canonical_key("http://example.com/a/")
    assert classify_source("https://docs.python.org/3/") == SourceType.OFFICIAL_DOCS
    assert classify_source("https://github.com/x/y") == SourceType.GITHUB
    assert classify_difficulty("Advanced FastAPI in production") == 4
    assert classify_difficulty("Python for beginners") == 1
    assert classify_difficulty("Something") == 2


def test_freshness_decays():
    assert freshness(NOW, None, NOW) == pytest.approx(1.0)
    assert freshness(NOW - timedelta(days=730), None, NOW) == pytest.approx(0.5, abs=0.01)
    assert freshness(None, None, NOW) == 0.5


async def test_discover_filters_unsafe_dedupes_and_ranks(monkeypatch):
    import app.domains.resource_engine.service as svc
    monkeypatch.setattr(svc, "validate_url", lambda u: __import__("app.domains.resource_engine.url_safety", fromlist=["x"]).validate_url(u, lambda h: ["93.184.216.34"]))
    results = [C("FastAPI tutorial", "https://fastapi.tiangolo.com/tutorial/", "FastAPI official tutorial"),
               C("FastAPI tutorial dup", "https://www.fastapi.tiangolo.com/tutorial", "dup"),
               C("FastAPI blog", "https://medium.com/x/fastapi-post", "random fastapi blog"),
               C("evil", "http://169.254.169.254/latest", "fastapi fastapi"),
               C("unrelated cooking", "https://example.com/pasta", "pasta")]

    async def fetcher(url): return 404 if "medium" in url else 200
    s = ResourceService([Adapter("a", results), Adapter("down", fail=True)], fetcher, clock=lambda: NOW)
    out = await s.discover("sk", ["FastAPI"], learner_level=1)
    urls = [r.url for r, _ in out]
    assert not any("169.254" in u for u in urls)
    assert sum("fastapi.tiangolo.com" in u for u in urls) == 1
    assert out[0][0].source_type == SourceType.OFFICIAL_DOCS and out[0][0].trust_status == TrustStatus.VERIFIED
    assert not any("medium.com" in u for u in urls)  # unreachable -> score 0 -> dropped
    assert s.warnings == ["source 'down' unavailable"]
    scores = [x for _, x in out]
    assert scores == sorted(scores, reverse=True)


async def test_never_claims_verified_without_fetching():
    s = ResourceService([])
    r = s.normalise(C("t", "https://93.184.216.34/x"), "sk")
    assert (await s.verify(r)).trust_status == TrustStatus.UNVERIFIED and r.last_verified_at is None


def test_difficulty_fit_prefers_stretch():
    s = ResourceService()
    easy = s.normalise(C("Python for beginners", "https://93.184.216.34/a"), "p")
    adv = s.normalise(C("Advanced Python internals", "https://93.184.216.34/b"), "p")
    assert score(easy, ["python"], 0, NOW) > score(adv, ["python"], 0, NOW)
    assert score(adv, ["python"], 3, NOW) > score(easy, ["python"], 3, NOW)
