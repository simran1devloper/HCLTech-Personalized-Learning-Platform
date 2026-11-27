"""Process-wide ModelRouter (built lazily so importing never needs credentials or a running model server)."""
from __future__ import annotations

from app.ai.factory import build_router
from app.ai.model_router import ModelRouter
from app.core.config import get_settings

_router: ModelRouter | None = None


def get_router() -> ModelRouter:
    global _router
    if _router is None:
        _router = build_router(get_settings())
    return _router


def reset_router() -> None:
    global _router
    _router = None
