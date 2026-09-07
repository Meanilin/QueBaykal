"""HTTP API (FastAPI).

Issue #13: FastAPI app skeleton with /health, lifespan-managed DB engine,
correlation_id middleware, exception handlers.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import orjson
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.config import APISettings, get_settings
from core.exceptions import AppError
from core.logging import (
    bind_context,
    clear_context,
    configure_logging,
    get_correlation_id,
    get_logger,
    set_correlation_id,
)
from db import create_engine, create_session_factory, get_session

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup/shutdown.

    Owns the DB engine, session factory, and any long-lived clients.
    """
    settings = get_settings()
    configure_logging(
        level=settings.logging.level,
        json_output=settings.logging.json_output or settings.environment == "prod",
        include_timestamp=settings.logging.include_timestamp,
        service_name=settings.service_name,
    )
    log.info("api_starting", environment=settings.environment, port=settings.api.port)
    engine = create_engine(settings.database)
    factory = create_session_factory(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = factory
    try:
        yield
    finally:
        log.info("api_stopping")
        await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="QueBaykal API",
        version="0.1.0",
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Health ---
    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["meta"])
    async def ready(request: Request) -> dict[str, str]:
        """Readiness — checks DB connectivity."""
        factory: async_sessionmaker = request.app.state.session_factory
        async with factory() as session:
            await session.execute(__import__("sqlalchemy").text("SELECT 1"))
        return {"status": "ready"}

    # --- Exception handlers ---
    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> ORJSONResponse:
        log.warning("app_error", error=str(exc), status=exc.http_status)
        return ORJSONResponse(
            status_code=exc.http_status,
            content={"error": exc.user_message},
        )

    # --- Middleware ---
    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        """Set correlation_id for the request, log timing."""
        cid = request.headers.get("X-Correlation-Id") or None
        cid = set_correlation_id(cid)
        clear_context()
        started = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception as exc:
            log.exception("unhandled_error", path=request.url.path, error=str(exc))
            return ORJSONResponse(
                status_code=500,
                content={"error": "Internal server error", "correlation_id": cid},
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        response.headers["X-Correlation-Id"] = cid
        log.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round(elapsed_ms, 2),
        )
        clear_context()
        return response

    # --- Routers (will be populated by future epics) ---
    # from api.routers import bookings, voting, agent
    # app.include_router(bookings.router, prefix="/api/bookings", tags=["bookings"])
    # app.include_router(voting.router, prefix="/api/voting", tags=["voting"])
    # app.include_router(agent.router, prefix="/api/agent", tags=["agent"])

    return app


__all__ = ["create_app", "lifespan"]


# Avoid unused import warnings during incremental rollout.
_ = (APISettings, bind_context, get_correlation_id, orjson, get_session)
