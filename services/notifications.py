"""Notifications service — reminders, alerts, booking/voting notifications.

Issues #75-#80: personal reminders, group reminders, mode change alerts,
overrun alerts, admin toggle, voting phase reminders.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Booking,
    BookingStatus,
    Chat,
    ChatMember,
    VoteSession,
    VoteSessionState,
)
from services.scheduler import SchedulerManager

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# Default reminder times
PERSONAL_REMINDER_MINUTES = 30
GROUP_REMINDER_MINUTES = 60


# --- Chat settings for reminders ---

def get_chat_reminder_settings(settings_json: str | None) -> dict:
    """Parse chat settings JSON for reminder preferences."""
    import json
    if not settings_json:
        return {"reminders_enabled": True, "personal_reminder_min": 30, "group_reminder_min": 60}
    try:
        return json.loads(settings_json)
    except (json.JSONDecodeError, TypeError):
        return {"reminders_enabled": True, "personal_reminder_min": 30, "group_reminder_min": 60}


async def update_chat_reminder_settings(
    session: AsyncSession,
    chat_id: int,
    enabled: bool | None = None,
    personal_min: int | None = None,
    group_min: int | None = None,
) -> Chat:
    """Update chat reminder settings."""
    q = select(Chat).where(Chat.telegram_id == chat_id)
    chat = (await session.execute(q)).scalar_one_or_none()
    if not chat:
        raise NotificationError(f"Chat {chat_id} not found")

    settings = get_chat_reminder_settings(chat.settings_json)
    if enabled is not None:
        settings["reminders_enabled"] = enabled
    if personal_min is not None:
        settings["personal_reminder_min"] = personal_min
    if group_min is not None:
        settings["group_reminder_min"] = group_min

    chat.settings_json = json.dumps(settings)
    await session.flush()
    return chat


class NotificationError(Exception):
    pass


# --- Personal reminder (30 min before booking_start) ---

async def schedule_booking_reminders(
    session: AsyncSession,
    booking_id: int,
    scheduler_manager: SchedulerManager,
) -> None:
    """Schedule both personal and group reminders for a booking."""
    q = select(Booking).where(Booking.id == booking_id)
    booking = (await session.execute(q)).scalar_one_or_none()
    if not booking:
        raise NotificationError(f"Booking {booking_id} not found")

    if booking.status != BookingStatus.PENDING:
        return

    # Get chat settings
    q = select(Chat).where(Chat.id == booking.chat_id)
    chat = (await session.execute(q)).scalar_one_or_none()
    settings = get_chat_reminder_settings(chat.settings_json if chat else None)

    if not settings.get("reminders_enabled", True):
        return

    personal_min = settings.get("personal_reminder_min", PERSONAL_REMINDER_MINUTES)
    group_min = settings.get("group_reminder_min", GROUP_REMINDER_MINUTES)

    # Schedule group reminder
    group_time = booking.booking_start - timedelta(minutes=group_min)
    if group_time > datetime.now(timezone.utc):
        await scheduler_manager.schedule_once(
            f"reminder_group_{booking_id}",
            "services.notifications:send_group_reminder",
            group_time,
            {"booking_id": booking_id},
        )

    # Schedule personal reminder for creator
    personal_time = booking.booking_start - timedelta(minutes=personal_min)
    if personal_time > datetime.now(timezone.utc):
        q = select(ChatMember).where(
            ChatMember.chat_id == booking.chat_id,
            ChatMember.user_id == booking.created_by_user_id,
        )
        member = (await session.execute(q)).scalar_one_or_none()
        if member:
            await scheduler_manager.schedule_once(
                f"reminder_personal_{booking_id}_{booking.created_by_user_id}",
                "services.notifications:send_personal_reminder",
                personal_time,
                {
                    "booking_id": booking_id,
                    "user_id": booking.created_by_user_id,
                    "minutes_before": personal_min,
                },
            )


async def send_personal_reminder(booking_id: int, user_id: int, minutes_before: int):
    """Send personal reminder to user via DM."""
    # This would be called by APScheduler
    # The bot handler would need to get the user's Telegram ID
    log.info("personal_reminder_scheduled", booking_id=booking_id, user_id=user_id, minutes=minutes_before)
    # Actual message sent in notification handler
    from bot.notifiers import notification_dispatcher
    await notification_dispatcher.send_personal_reminder(booking_id, user_id, minutes_before)


async def send_group_reminder(booking_id: int):
    """Send group reminder to chat."""
    log.info("group_reminder_scheduled", booking_id=booking_id)
    from bot.notifiers import notification_dispatcher
    await notification_dispatcher.send_group_reminder(booking_id)


# --- Mode change notification ---

async def notify_mode_change(
    session: AsyncSession,
    vote_session: VoteSession,
    old_mode: str,
    new_mode: str,
) -> str:
    """Notify chat about booking mode change (planned → instant)."""
    q = select(Chat).where(Chat.id == vote_session.chat_id)
    chat = (await session.execute(q)).scalar_one()

    return (
        f"⚠️ Режим брони изменён!\n"
        f"Было: {old_mode}\n"
        f"Стало: {new_mode}\n"
        f"Сессия #{vote_session.id}"
    )


# --- Overrun notification ---

async def notify_overrun(
    session: AsyncSession,
    vote_session: VoteSession,
) -> str:
    """Notify chat about overtime."""
    q = select(Chat).where(Chat.id == vote_session.chat_id)
    chat = (await session.execute(q)).scalar_one()

    return (
        f"⏰ Фильм шёл дольше запланированного!\n"
        f"Сессия #{vote_session.id}\n"
        f"Проверьте, ещё кто-то смотрит?"
    )


# --- Voting phase reminders ---

async def schedule_voting_reminders(
    session: AsyncSession,
    vote_session_id: int,
    scheduler_manager: SchedulerManager,
) -> None:
    """Schedule reminders for voting phases."""
    q = select(VoteSession).where(VoteSession.id == vote_session_id)
    vs = (await session.execute(q)).scalar_one_or_none()
    if not vs:
        raise NotificationError(f"VoteSession {vote_session_id} not found")

    if not vs.suggest_duration:
        return

    # Reminder: 2 min before suggest ends
    suggest_end = vs.started_at + timedelta(minutes=vs.suggest_duration)
    suggest_warn = suggest_end - timedelta(minutes=2)

    if suggest_warn > datetime.now(timezone.utc):
        await scheduler_manager.schedule_once(
            f"vote_warn_suggest_{vote_session_id}",
            "services.notifications:voting_phase_reminder",
            suggest_warn,
            {"vote_session_id": vote_session_id, "phase": "suggest"},
        )

    # Reminder: 2 min before final vote ends
    if vs.vote_duration and vs.suggest_duration:
        # Approximate: suggest + filter + vote
        final_vote_end = vs.started_at + timedelta(
            minutes=vs.suggest_duration + 5 + vs.vote_duration
        )
        final_warn = final_vote_end - timedelta(minutes=2)

        if final_warn > datetime.now(timezone.utc):
            await scheduler_manager.schedule_once(
                f"vote_warn_final_{vote_session_id}",
                "services.notifications:voting_phase_reminder",
                final_warn,
                {"vote_session_id": vote_session_id, "phase": "final"},
            )


async def voting_phase_reminder(vote_session_id: int, phase: str):
    """Send voting phase reminder."""
    from bot.notifiers import notification_dispatcher
    await notification_dispatcher.voting_phase_reminder(vote_session_id, phase)


# --- Admin reminder toggle ---

async def set_reminders_enabled(
    session: AsyncSession,
    chat_id: int,
    enabled: bool,
    is_admin: bool,
) -> Chat:
    """Toggle reminders for a chat (admin only)."""
    q = select(Chat).where(Chat.telegram_id == chat_id)
    chat = (await session.execute(q)).scalar_one_or_none()
    if not chat:
        raise NotificationError(f"Chat {chat_id} not found")

    if not is_admin:
        raise NotificationPermissionError("Только админ может изменить настройки оповещений")

    settings = get_chat_reminder_settings(chat.settings_json)
    settings["reminders_enabled"] = enabled
    chat.settings_json = json.dumps(settings)
    await session.flush()
    return chat


class NotificationPermissionError(Exception):
    pass


__all__ = [
    "NotificationError",
    "NotificationPermissionError",
    "PERSONAL_REMINDER_MINUTES",
    "GROUP_REMINDER_MINUTES",
    "get_chat_reminder_settings",
    "update_chat_reminder_settings",
    "schedule_booking_reminders",
    "send_personal_reminder",
    "send_group_reminder",
    "notify_mode_change",
    "notify_overrun",
    "schedule_voting_reminders",
    "voting_phase_reminder",
    "set_reminders_enabled",
]