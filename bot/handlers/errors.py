"""Global error handler for aiogram dispatcher.

Catches everything that escapes handlers and logs with correlation_id.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import ErrorEvent, TelegramObject

from core.exceptions import AppError, PermissionDeniedError, ValidationError
from core.logging import get_logger

log = get_logger(__name__)


def register(dp) -> None:  # type: ignore[no-untyped-def]
    @dp.error()
    async def on_error(event: ErrorEvent) -> None:
        exc = event.exception
        if isinstance(exc, PermissionDeniedError):
            log.warning("permission_denied", error=str(exc))
            if event.update.message:
                await event.update.message.answer("🚫 Недостаточно прав.")
            return
        if isinstance(exc, ValidationError):
            log.info("validation_error", error=str(exc))
            if event.update.message:
                await event.update.message.answer(f"⚠️ {exc.user_message}")
            return
        if isinstance(exc, AppError):
            log.warning("app_error", error=str(exc), status=exc.http_status)
            if event.update.message:
                await event.update.message.answer(exc.user_message)
            return
        log.exception("unhandled_error", error=str(exc))
        if event.update.message:
            try:
                await event.update.message.answer(
                    "❌ Внутренняя ошибка. Разработчики уведомлены."
                )
            except Exception:
                pass


__all__ = ["register"]
