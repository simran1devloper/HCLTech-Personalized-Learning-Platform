"""New quiz questions are written by several simultaneous calls on a hosted provider, saved as each call returns."""
import asyncio
import json

import pytest

from app.ai import runtime
from app.ai.types import ModelCapabilities
from app.core.config import get_settings
from app.core.container import get_container
from app.domains.assessments.bank import generator as gen
from app.domains.assessments.bank.service import CONCEPT_AREAS, QuestionBankService
from tests.conftest import make_token
from tests.integration.test_diagnostic_study import fake_json

D = "/api/v1/diagnostic-study"
H = {"Authorization": f"Bearer {make_token('parallel-user')}"}


class Hosted:
    def __init__(self, name="openrouter", local=False):
        self.name, self.caps = name, ModelCapabilities(structured_output=True, local=local)

    def model_capabilities(self):
        return self.caps


class Model:
    """Replaces the model call behind `gen.router_call`; counts overlap and can fail chosen calls."""
    def __init__(self, delay=0.05, fail=(), payload=None):
        self.prompts, self.delay, self.fail, self.payload = [], delay, set(fail), payload
        self.running = self.peak = self.cancelled = 0

    async def __call__(self, prompt, on_item=None):
        self.prompts.append(prompt)
        call = len(self.prompts)
        n = int(prompt.split("exactly ")[1].split(" ")[0])
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await asyncio.sleep(self.delay if call != 1 else self.delay / 2)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.running -= 1
        if call in self.fail or "all" in self.fail:
            raise RuntimeError("provider down")
        return (self.payload or fake_json(n, base=f"batch {call}", concept="Core concepts")), 100, 200


@pytest.fixture
def hosted(client, monkeypatch):
    client.get("/health")
    m = Model()
    fake_router = type("R", (), {"providers": [Hosted()]})()
    monkeypatch.setattr(runtime, "get_router", lambda: fake_router)
    monkeypatch.setattr(gen, "router_call", m)
    bank = get_container().bank
    bank.call = None
    return m


def skill(name="Zig Language"):
    return get_container().bank.resolve_or_register(name)[0]


async def generate(missing=10, name="Zig Language", **kw):
    s = skill(name)
    r = await get_container().bank.generate_missing_questions(s.name, s.skill_id, missing, concepts=[], **kw)
    return r, get_container().assessments.candidates(s.skill_id)


# ---------------- planning ----------------
def test_batches_ask_for_spares_and_split_difficulties_and_areas():
    batches = QuestionBankService.split_batches(10, 4, [1, 2, 3, 4, 5, 1, 2, 3, 4, 5], [])
    assert [n for n, _, _ in batches] == [3, 3, 3, 3] and all(len(d) == n for n, d, _ in batches)
    assert [a for _, _, a in batches] == [[x] for x in CONCEPT_AREAS]                 # a new topic: one distinct area per call
    assert sorted({x for _, d, _ in batches for x in d}) == [1, 2, 3, 4, 5]
    known = QuestionBankService.split_batches(10, 4, [3] * 10, ["Indexes", "Joins"])
    assert {a[0] for _, _, a in known} == {"Indexes", "Joins"}
    assert [n for n, _, _ in QuestionBankService.split_batches(5, 4, [3] * 5, [])] == [3, 3]


@pytest.mark.parametrize("provider,missing,expected", [("openrouter", 10, 4), ("openrouter", 5, 2), ("openrouter", 3, 1), ("ollama", 10, 1)])
def test_only_hosted_providers_get_parallel_calls(client, monkeypatch, provider, missing, expected):
    client.get("/health")
    monkeypatch.setattr(runtime, "get_router", lambda: type("R", (), {"providers": [Hosted(provider)]})())
    assert QuestionBankService(get_container().assessments).parallel_batches(missing) == expected


def test_parallel_calls_can_be_switched_off(client, monkeypatch):
    client.get("/health")
    monkeypatch.setattr(runtime, "get_router", lambda: type("R", (), {"providers": [Hosted()]})())
    bank = QuestionBankService(get_container().assessments)
    for setting, value in (("QUESTION_PARALLEL_BATCHES", 1), ("QUESTION_BATCH_SIZE", 5)):
        with monkeypatch.context() as m:
            m.setattr(get_settings(), setting, value)
            assert bank.parallel_batches(10) == 1
    assert QuestionBankService(get_container().assessments, call=lambda p: None).parallel_batches(10) == 1


# ---------------- generation ----------------
async def test_calls_overlap_and_spares_are_kept_for_next_time(hosted):
    seen = []
    r, stored = await generate(10, on_batch=seen.append)
    assert hosted.peak == 4 and r.llm_calls == 4 and r.added == 10 and r.error is None
    assert len(stored) >= 10 and len({q["id"] for q in stored}) == len(stored)
    assert seen == sorted(seen) and seen[-1] == 10 and len(seen) >= 3                  # progress after each call, never above the target
    assert all("Core concepts" not in p for p in hosted.prompts[1:2])                  # calls were given different areas


async def test_one_failed_call_keeps_the_others_and_tops_up(hosted, monkeypatch):
    hosted.fail = {2, 5}          # one batch fails twice (the call and its single retry)

    real_sleep = asyncio.sleep
    monkeypatch.setattr("app.domains.assessments.bank.service.asyncio.sleep", lambda s: real_sleep(0 if s == 2 else s))
    r, stored = await generate(10)
    assert r.added == 10 and r.error is None and len(stored) >= 10


async def test_every_call_failing_reports_the_provider(hosted, monkeypatch):
    hosted.fail = {"all"}
    real_sleep = asyncio.sleep
    monkeypatch.setattr("app.domains.assessments.bank.service.asyncio.sleep", lambda s: real_sleep(0 if s == 2 else s))
    r, stored = await generate(10)
    assert r.added == 0 and r.error == "provider" and stored == [] and r.llm_calls == 4


async def test_unusable_answers_stop_after_two_rounds(hosted):
    hosted.payload = json.dumps({"questions": []})
    r, _ = await generate(10)
    assert r.error == "invalid_output" and r.llm_calls <= 8


async def test_running_calls_are_cancelled_when_the_caller_has_enough(hosted):
    taken = []

    def take(qid, q, created):
        taken.append(qid)
        return True
    r, _ = await generate(10, on_question=take, should_stop=lambda: len(taken) >= 3)
    assert r.added <= 6 and hosted.cancelled >= 1 and hosted.running == 0


async def test_stop_cancels_every_running_call(hosted):
    hosted.delay = 5
    task = asyncio.create_task(generate(10))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert hosted.cancelled == 4 and hosted.running == 0


def test_a_new_topic_quiz_through_the_api(client, hosted):
    r = client.post(f"{D}/start", json={"topic": "Zig Language", "question_count": 10}, headers=H)
    assert r.status_code == 201, r.text
    body = r.json()
    texts = [q["prompt"] for q in body["questions"]]
    assert body["question_count"] == 10 and len(set(texts)) == 10 and hosted.peak == 4
    again = client.post(f"{D}/start", json={"topic": "Zig Language", "question_count": 10}, headers=H)
    assert again.json()["generation_source"] == "question_bank" and len(hosted.prompts) == 4
