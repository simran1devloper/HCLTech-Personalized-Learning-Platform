"""New-API application factory. Mounted by the legacy `main.py` so /auth and /learning keep working."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.v1.assessment_routes import router as assessment_router
from app.api.v1.diagnostic_routes import router as diagnostic_router
from app.api.v1.extra_routes import router as extra_router
from app.api.v1.history_routes import router as history_router
from app.api.v1.routes import router as v1_router
from app.api.v1.skill_explorer_routes import router as explorer_router
from app.api.v1.tutor_routes import router as tutor_router
from app.api.v1.workspace_routes import router as workspace_router
from app.core.exceptions import AppError, error_body
from app.observability.metrics import metrics

log = logging.getLogger("app")


def install(app: FastAPI) -> None:
    app.include_router(explorer_router)  # first: its fixed paths (/skills/search) must win over /skills/{skill_id}
    app.include_router(v1_router)
    app.include_router(assessment_router)
    app.include_router(diagnostic_router)
    app.include_router(extra_router)
    app.include_router(tutor_router)
    app.include_router(history_router)
    app.include_router(workspace_router)

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        headers = {"Retry-After": str(int(exc.details.get("retry_after_s", 1)) or 1)} if exc.status_code == 429 else None
        return JSONResponse(error_body(exc.code, exc.message, exc.details), exc.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        if not request.url.path.startswith("/api/v1"):  # legacy routes keep FastAPI's default 422 shape
            from fastapi.exception_handlers import request_validation_exception_handler
            return await request_validation_exception_handler(request, exc)
        errs = [{"loc": [str(x) for x in e["loc"]], "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(error_body("validation_failed", "request validation failed", {"errors": errs}), 422)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)  # details to logs only, never to the client
        return JSONResponse(error_body("internal_error", "internal server error"), 500)

    @app.get("/metrics", include_in_schema=False)
    async def _metrics():  # restrict at the network/ingress layer in production
        return PlainTextResponse(metrics.render())
