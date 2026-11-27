import pytest
from pydantic import ValidationError

from app.core.config import AppSettings

FLAGS = {"CONTENT_PIPELINE": "legacy", "GENERATION_BACKEND": "inline", "CACHE_BACKEND": "memory",
         "STORE_BACKEND": "sqlite", "ATOM_REUSE": "off", "AGENT_PIPELINE": "off", "REVIEW_GATES": "off"}


def test_defaults_keep_current_behaviour(monkeypatch):
    for name in FLAGS:                                   # defaults must hold regardless of the surrounding environment
        monkeypatch.delenv(name, raising=False)
    s = AppSettings(_env_file=None)
    assert {k: getattr(s, k) for k in FLAGS} == FLAGS


@pytest.mark.parametrize("flag,bad", [("CONTENT_PIPELINE", "v3"), ("GENERATION_BACKEND", "celery"), ("CACHE_BACKEND", "memcached"),
                                     ("STORE_BACKEND", "mysql"), ("ATOM_REUSE", "yes"), ("AGENT_PIPELINE", "true"), ("REVIEW_GATES", "maybe")])
def test_invalid_flag_values_are_rejected_at_startup(flag, bad):
    with pytest.raises(ValidationError):
        AppSettings(_env_file=None, **{flag: bad})


def test_flags_can_be_switched_from_the_environment(monkeypatch):
    monkeypatch.setenv("CONTENT_PIPELINE", "v2"); monkeypatch.setenv("STORE_BACKEND", "postgres")
    s = AppSettings(_env_file=None)
    assert s.CONTENT_PIPELINE == "v2" and s.STORE_BACKEND == "postgres" and s.CACHE_BACKEND == "memory"
