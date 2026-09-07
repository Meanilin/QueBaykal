"""Notification handlers for Telegram bot.

Issues #75-#80: reminder commands, toggle, booking/voting reminders.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Chat,
    ChatMember,
    VoteSession,
    VoteSessionState,
)
from services import notifications as notif_service

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

router = Router(name="notifications")


# --- Helper: check admin ---

async def _is_chat_admin(session: AsyncSession, chat_id: int, user_id: int) -> bool:
    q = select(ChatMember).where(
        ChatMember.chat_id == chat_id,
        ChatMember.user_id == user_id,
        ChatMember.role.in_(["admin", "root"]),
    )
    return (await session.execute(q)).scalar_one_or_none() is not None


# --- /set_reminders ---

@router.message(Command("set_reminders"))
async def cmd_set_reminders(message: Message, command: CommandObject, session: AsyncSession):
    """Toggle reminders for this chat (admin only).
    
    Usage: /set_reminders on|off
    """
    args = (command.args or "").strip().lower()
    if args not in ("on", "off", "true", "false", "1", "0"):
        await message.answer("Использование: /set_reminders on|off")
        return

    enabled = args in ("on", "true", "1")

    # Check admin
    q = select(Chat).where(Chat.telegram_id == message.chat.id)
    chat = (await session.execute(q)).scalar_one_or_none()
    if not chat:
        await message.answer("Чат не найден в БД.")
        return

    is_admin = await _is_chat_admin(session, chat.id, message.from_user.id)
    if not is_admin:
        await message.answer("Только админ чата может изменить настройки оповещений.")
        return

    try:
        chat = await notif_service.set_reminders_enabled(
            session,
            chat.id,
            enabled,
            is_admin=True,
        )
        await message.answer(
            f"✅ Оповещения {'включены' if enabled else 'выключены'} для этого чата."
        )
    except notif_service.NotificationError as e:
        await message.answer(str(e))


# --- /set_reminder_time ---

@router.message(Command("set_reminder_time"))
async def cmd_set_reminder_time(message: Message, command: CommandObject, session: AsyncSession):
    """Set personal/group reminder times.
    
    Usage: /set_reminder_time personal <minutes>
           /set_reminder_time group <minutes>
    """
    args = (command.args or "").strip().split()
    if len(args) != 2:
        await message.answer(
            "Использование:\n"
            "/set_reminder_time personal <мин>\n"
            "/set_reminder_time group <мин>"
        )
        return

    rem_type, minutes_str = args
    if rem_type not in ("personal", "group"):
        await message.answer("Тип должен быть: personal или group")
        return

    if not minutes_str.isdigit():
        await message.answer("Минуты должны быть числом")
        return

    minutes = int(minutes_str)
    if not (1 <= minutes <= 1440):
        await message.answer("Минуты: 1-1440 (1 день)")
        return

    q = select(Chat).where(Chat.telegram_id == message.chat.id)
    chat = (await session.execute(q)).scalar_one_or_none()
    if not chat:
        await message.answer("Чат не найден в БД.")
        return

    is_admin = await _is_chat_admin(session, chat.id, message.from_user.id)
    if not is_admin:
        await message.answer("Только админ чата может изменить настройки оповещений.")
        return

    if rem_type == "personal":
        chat = await notif_service.update_chat_reminder_settings(
            session, chat.id, personal_min=minutes
        )
    else:
        chat = await notif_service.update_chat_reminder_settings(
            session, chat.id, group_min=minutes
        )

    await message.answer(f"✅ Время {rem_type}-оповещения установлено: {minutes} мин.")


# --- /reminder_status ---

@router.message(Command("reminder_status"))
async def cmd_reminder_status(message: Message, session: AsyncSession):
    """Show current reminder settings for this chat."""
    q = select(Chat).where(Chat.telegram_id == message.chat.id)
    chat = (await session.execute(q)).scalar_one_or_none()
    if not chat:
        await message.answer("Чат не найден в БД.")
        return

    settings = notif_service.get_chat_reminder_settings(chat.settings_json)

    await message.answer(
        f"🔔 Настройки оповещений:\n"
        f"Статус: {'включены' if settings.get('reminders_enabled') else 'выключены'}\n"
        f"Личное напоминание: за {settings.get('personal_reminder_min', 30)} мин\n"
        f"Групповое напоминание: за {settings.get('group_reminder_min', 60)} мин"
    )


# --- Test reminder commands ---

@router.message(Command("test_reminder"))
async def cmd_test_reminder(message: Message, session: AsyncSession):
    """Test personal reminder (admin only)."""
    q = select(Chat).where(Chat.telegram_id == message.chat.id)
    chat = (await session.execute(q)).scalar_one_or_none()
    if not chat:
        await message.answer("Чат не найден в БД.")
        return

    is_admin = await _is_chat_admin(session, chat.id, message.from_user.id)
    if not is_admin:
        await message.answer("Только админ.")
        return

    from bot.notifiers import notification_dispatcher
    await notification_dispatcher.send_personal_reminder(
        session, 0, message.from_user.id, 30
    )
    await message.answer("Тестовое личное напоминание отправлено в ЛС.")


@router.message(Command("test_group_reminder"))
async def cmd_test_group_reminder(message: Message, session: AsyncSession):
    """Test group reminder (admin only)."""
    q = select(Chat).where(Chat.telegram_id == message.chat.id)
    chat = (await session.execute(q)).scalar_one_or_none()
    if not chat:
        await message.answer("Чат не найден в БД.")
        return

    is_admin = await _is_chat_admin(session, chat.id, message.from_user.id)
    if not is_admin:
        await message.answer("Только админ.")
        return

    from bot.notifiers import notification_dispatcher
    await notification_dispatcher.send_group_reminder(session, 0)
    await message.answer("Тестовое групповое напоминание отправлено в чат.")


def register(dp):
    dp.include_router(router)