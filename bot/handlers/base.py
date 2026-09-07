"""Base handlers: /start, /help, /status.

In Epic 0 only the skeleton is registered. Real handlers land in
Epic 1 (booking) and Epic 2 (voting).
"""

from __future__ import annotations

from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)


def register(dp: Dispatcher) -> None:
    @dp.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        if not message.from_user:
            return
        log.info("cmd_start", user=message.from_user.id)
        await message.answer(
            f"Привет, {message.from_user.first_name or 'друг'}! "
            "Я — QueBaykal, бот для выбора фильма и управления общим телевизором.\n\n"
            "Команда /help покажет справку."
        )

    @dp.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        log.info("cmd_help", user=message.from_user.id if message.from_user else None)
        await message.answer(
            "🔹 /start — приветствие\n"
            "🔹 /help — эта справка\n"
            "🔹 /status — состояние системы\n\n"
            "Полный список команд появится после Epic 1 (Booking) и Epic 2 (Voting)."
        )

    @dp.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        """Minimal status: env, version, admin status of the user."""
        settings = get_settings()
        is_admin = (
            message.from_user is not None
            and message.from_user.id in settings.telegram.admin_ids
        )
        log.info(
            "cmd_status",
            user=message.from_user.id if message.from_user else None,
            admin=is_admin,
        )
        await message.answer(
            f"📊 Статус\n"
            f"• Среда: <code>{settings.environment}</code>\n"
            f"• Режим: <code>{settings.telegram.bot_mode}</code>\n"
            f"• Вы админ: <code>{'да' if is_admin else 'нет'}</code>"
        )

    # Fallback for unknown text
    @dp.message(F.text)
    async def fallback_text(message: Message) -> None:
        log.info("unknown_text", text=(message.text or "")[:64])
        await message.answer("Неизвестная команда. Попробуйте /help.")


__all__ = ["register"]
