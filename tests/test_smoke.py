"""Smoke tests verifying Epic 0 wiring without external services.

These tests do not need Postgres or Redis.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from api import create_app
from api.app import lifespan
from bot.factory import create_bot, create_dispatcher, create_storage
from core.config import get_settings
from core.exceptions import (
    AppError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from core.logging import bind_context, clear_context, configure_logging, get_logger
from models import (
    Base,
    Chat,
    ChatMember,
    ChatTVBinding,
    ChatTVBindingStatus,
    TVDevice,
    TVDeviceStatus,
    User,
    UserRole,
)


def test_settings_load():
    s = get_settings()
    assert s.environment in {"dev", "staging", "prod"}
    assert s.telegram.parse_mode in {"HTML", "Markdown", "MarkdownV2"}


def test_logging_emits():
    configure_logging("INFO", json_output=False)
    log = get_logger("test")
    log.info("smoke", key="value")  # does not raise
    clear_context()


def test_exceptions_have_status():
    for cls, status in [
        (NotFoundError, 404),
        (PermissionDeniedError, 403),
        (ValidationError, 422),
        (ConflictError, 409),
    ]:
        e = cls()
        assert e.http_status == status


def test_models_metadata():
    tables = Base.metadata.tables
    assert {"users", "chats", "chat_members", "tv_devices", "chat_tv_bindings"} <= set(
        tables
    )


def test_bot_factory():
    # Use a structurally-valid token for the smoke test (9:name:34hex).
    import os
    os.environ["TG_BOT_TOKEN"] = "1234567890:" + "A" * 35
    get_settings.cache_clear()  # type: ignore[attr-defined]
    bot = create_bot()
    assert bot is not None
    dp = create_dispatcher()
    assert dp is not None
    storage = create_storage()
    assert storage is not None


@pytest.mark.asyncio
async def test_api_health():
    app = create_app()
    transport = ASGITransport(app=app)
    async with lifespan(app):
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/health")
            assert r.status_code == 200
            assert r.json() == {"status": "ok"}
            assert "X-Correlation-Id" in r.headers
