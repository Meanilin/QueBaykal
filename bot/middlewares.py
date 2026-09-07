"""Bot middlewares.

Issue #16 (correlation_id) implemented here for aiogram.
Issue #14: required infrastructure for all routers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from core.logging import bind_context, clear_context, set_correlation_id


class CorrelationMiddleware(BaseMiddleware):
    """Set per-update correlation_id and clear context after handling."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        set_correlation_id()
        try:
            return await handler(event, data)
        finally:
            clear_context()


class UserContextMiddleware(BaseMiddleware):
    """Bind user_id, chat_id to logging context."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id: int | None = None
        chat_id: int | None = None
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            chat_id = event.chat.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
            if event.message and hasattr(event.message, "chat"):
                chat_id = event.message.chat.id  # type: ignore[union-attr]
        bind_context(user_id=user_id, chat_id=chat_id)
        return await handler(event, data)


__all__ = ["CorrelationMiddleware", "UserContextMiddleware"]
