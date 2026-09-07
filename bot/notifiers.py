"""Notification dispatcher — sends actual Telegram messages.

Called by scheduler jobs and handlers.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Booking,
    Chat,
    ChatMember,
    User,
    VoteSession,
)
from services import notifications as notif_service

if TYPE_CHECKING:
    from aiogram import Bot

log = logging.getLogger(__name__)


class NotificationDispatcher:
    """Dispatch notifications via Telegram bot."""

    def __init__(self, bot: "Bot" = None):
        self.bot = bot

    def set_bot(self, bot: "Bot"):
        self.bot = bot

    async def send_personal_reminder(
        self,
        session: AsyncSession,
        booking_id: int,
        user_id: int,
        minutes_before: int,
    ):
        """Send personal reminder DM to user."""
        if not self.bot:
            log.warning("no_bot_for_personal_reminder")
            return

        q = select(Booking).where(Booking.id == booking_id)
        booking = (await session.execute(q)).scalar_one_or_none()
        if not booking:
            return

        q = select(User).where(User.id == user_id)
        user = (await session.execute(q)).scalar_one_or_none()
        if not user:
            return

        q = select(Chat).where(Chat.id == booking.chat_id)
        chat = (await session.execute(q)).scalar_one_or_none()
        if not chat:
            return

        try:
            await self.bot.send_message(
                user.telegram_id,
                f"⏰ Напоминание: через {minutes_before} мин начинается бронирование ТВ\n"
                f"Чат: {chat.title or chat.telegram_id}\n"
                f"Время: {booking.booking_start.strftime('%H:%M')}\n"
                f"Длительность: ~{booking.movie_duration_estimate or 120} мин",
            )
            log.info("personal_reminder_sent", user_id=user.telegram_id, booking_id=booking_id)
        except Exception as e:
            log.error("personal_reminder_failed", user_id=user.telegram_id, error=str(e))

    async def send_group_reminder(
        self,
        session: AsyncSession,
        booking_id: int,
    ):
        """Send group reminder to chat."""
        if not self.bot:
            log.warning("no_bot_for_group_reminder")
            return

        q = select(Booking).where(Booking.id == booking_id)
        booking = (await session.execute(q)).scalar_one_or_none()
        if not booking:
            return

        q = select(Chat).where(Chat.id == booking.chat_id)
        chat = (await session.execute(q)).scalar_one_or_none()
        if not chat:
            return

        try:
            await self.bot.send_message(
                chat.telegram_id,
                f"⏰ Напоминание: через 1 час начинается бронирование ТВ!\n"
                f"Время: {booking.booking_start.strftime('%H:%M')}\n"
                f"Длительность: ~{booking.movie_duration_estimate or 120} мин\n"
                f"Создатель: @{booking.creator.username or booking.created_by_user_id}",
            )
            log.info("group_reminder_sent", chat_id=chat.telegram_id, booking_id=booking_id)
        except Exception as e:
            log.error("group_reminder_failed", chat_id=chat.telegram_id, error=str(e))

    async def voting_phase_reminder(
        self,
        session: AsyncSession,
        vote_session_id: int,
        phase: str,
    ):
        """Send voting phase reminder."""
        if not self.bot:
            log.warning("no_bot_for_voting_reminder")
            return

        q = select(VoteSession).where(VoteSession.id == vote_session_id)
        vs = (await session.execute(q)).scalar_one_or_none()
        if not vs:
            return

        q = select(Chat).where(Chat.id == vs.chat_id)
        chat = (await session.execute(q)).scalar_one_or_none()
        if not chat:
            return

        phase_text = {
            "suggest": "предложения фильмов",
            "final": "финальное голосование",
        }.get(phase, phase)

        try:
            await self.bot.send_message(
                chat.telegram_id,
                f"🗳 Напоминание: фаза {phase_text} заканчивается через 2 минуты!\n"
                f"Сессия #{vs.id}",
            )
            log.info("voting_reminder_sent", chat_id=chat.telegram_id, vs_id=vote_session_id, phase=phase)
        except Exception as e:
            log.error("voting_reminder_failed", chat_id=chat.telegram_id, error=str(e))

    async def notify_mode_change(
        self,
        session: AsyncSession,
        vote_session: VoteSession,
        old_mode: str,
        new_mode: str,
    ):
        """Notify chat about mode change."""
        if not self.bot:
            return

        q = select(Chat).where(Chat.id == vote_session.chat_id)
        chat = (await session.execute(q)).scalar_one_or_none()
        if not chat:
            return

        try:
            await self.bot.send_message(
                chat.telegram_id,
                f"⚠️ Режим брони изменён!\n"
                f"Было: {old_mode}\n"
                f"Стало: {new_mode}\n"
                f"Сессия #{vote_session.id}",
            )
        except Exception as e:
            log.error("mode_change_notify_failed", error=str(e))

    async def notify_overrun(
        self,
        session: AsyncSession,
        vote_session: VoteSession,
    ):
        """Notify chat about overtime."""
        if not self.bot:
            return

        q = select(Chat).where(Chat.id == vote_session.chat_id)
        chat = (await session.execute(q)).scalar_one_or_none()
        if not chat:
            return

        try:
            await self.bot.send_message(
                chat.telegram_id,
                f"⏰ Фильм шёл дольше запланированного!\n"
                f"Сессия #{vote_session.id}\n"
                f"Проверьте, ещё кто-то смотрит?",
            )
        except Exception as e:
            log.error("overrun_notify_failed", error=str(e))


# Global dispatcher instance
notification_dispatcher = NotificationDispatcher()


__all__ = [
    "NotificationDispatcher",
    "notification_dispatcher",
]