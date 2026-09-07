"""Telegram bot handlers: booking commands.

Issues #20-#25, #27: /book, /book now, /my_bookings, /cancel_booking,
/confirm_booking, /free_slots
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from aiogram import Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from core.config import get_settings
from core.logging import get_logger
from models import BookingStatus, User
from services import bookings

if TYPE_CHECKING:
    from db.session import async_sessionmaker

log = get_logger(__name__)


def _parse_datetime(text: str) -> datetime | None:
    """Parse YYYY-MM-DD HH:MM or YYYY-MM-DDTHH:MM."""
    formats = ["%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_duration(text: str) -> int | None:
    """Parse duration in minutes (integer)."""
    try:
        val = int(text)
        return val if 1 <= val <= 720 else None  # 1 min to 12 hours
    except ValueError:
        return None


async def _get_or_create_user(session, tg_user) -> User:
    user = await session.get(User, tg_user.id)
    if not user:
        user = User(
            telegram_id=tg_user.id,
            username=tg_user.username,
            full_name=tg_user.full_name,
            language_code=tg_user.language_code,
        )
        session.add(user)
        await session.flush()
    return user


def register(dp: Dispatcher) -> None:
    @dp.message(Command("book"))
    async def cmd_book(message: Message, command: CommandObject) -> None:
        """Create a booking.

        Usage:
          /book YYYY-MM-DD HH:MM [duration_minutes]
          /book now [duration_minutes]
        """
        session_factory: async_sessionmaker = message.bot.data.get("session_factory")
        if not session_factory:
            await message.answer("⚠️ База данных недоступна")
            return

        if not message.from_user:
            return

        args = (command.args or "").strip().split()
        if not args:
            await message.answer(
                "Использование:\n"
                "/book YYYY-MM-DD HH:MM [длительность_мин]\n"
                "/book now [длительность_мин]"
            )
            return

        async with session_factory() as session:
            user = await _get_or_create_user(session, message.from_user)

            # Determine start time
            if args[0].lower() == "now":
                booking_start = datetime.now()
                duration_arg = args[1] if len(args) > 1 else None
            else:
                if len(args) < 2:
                    await message.answer("Нужны дата и время: /book YYYY-MM-DD HH:MM [длительность]")
                    return
                dt_str = f"{args[0]} {args[1]}"
                booking_start = _parse_datetime(dt_str)
                if not booking_start:
                    await message.answer("Неверный формат. Пример: /book 2026-12-31 20:00")
                    return
                duration_arg = args[2] if len(args) > 2 else None

            duration_min = _parse_duration(duration_arg) if duration_arg else 120
            if duration_min is None:
                duration_min = 120
            booking_end = booking_start + timedelta(minutes=duration_min)

            # Get TV device for this chat (requires chat_tv_bindings)
            from models import ChatTVBinding, TVDevice
            from sqlalchemy import select
            binding_q = select(ChatTVBinding).where(
                ChatTVBinding.chat_id == message.chat.id,
                ChatTVBinding.status == "active"
            )
            binding = (await session.execute(binding_q)).scalar_one_or_none()
            if not binding:
                await message.answer("❌ Чат не привязан к телевизору. Админ: /setup")
                return

            tv = await session.get(TVDevice, binding.tv_device_id)
            if not tv:
                await message.answer("❌ Привязанный телевизор не найден")
                return

            try:
                booking = await bookings.create_booking(
                    session,
                    chat_id=message.chat.id,
                    tv_device_id=tv.id,
                    booking_start=booking_start,
                    booking_end=booking_end,
                    created_by_user_id=user.id,
                    mode="instant",
                    movie_duration_estimate=duration_min,
                )
                await session.commit()

                # Schedule APScheduler jobs for this booking
                scheduler_manager = message.bot.data.get("scheduler_manager")
                if scheduler_manager:
                    await scheduler_manager.schedule_booking_jobs(booking)

                await message.answer(
                    f"✅ Бронь создана #{booking.id}\n"
                    f"📅 {booking_start.strftime('%Y-%m-%d %H:%M')} — "
                    f"{booking_end.strftime('%H:%M')}\n"
                    f"🎬 Ожидаемая длительность: {duration_min} мин\n"
                    f"⏳ Статус: <b>PENDING</b> — ждёт подтверждения за 30 мин до начала"
                )
            except bookings.BookingConflictError as e:
                await message.answer(
                    f"❌ Конфликт с бронью #{e.existing.id}\n"
                    f"Занято: {e.existing.booking_start.strftime('%H:%M')} — "
                    f"{e.existing.booking_end.strftime('%H:%M')}"
                )

    @dp.message(Command("my_bookings"))
    async def cmd_my_bookings(message: Message) -> None:
        session_factory: async_sessionmaker = message.bot.data.get("session_factory")
        if not session_factory:
            await message.answer("⚠️ База данных недоступна")
            return
        if not message.from_user:
            return

        async with session_factory() as session:
            user = await _get_or_create_user(session, message.from_user)
            bks = await bookings.list_my_bookings(session, user.id, limit=10)

        if not bks:
            await message.answer("У вас нет броней.")
            return

        lines = ["📋 Ваши брони:"]
        for b in bks:
            status_emoji = {
                "pending": "⏳", "confirmed": "✅", "active": "🎬",
                "overrun": "⚠️", "completed": "✅", "cancelled": "🚫",
                "expired": "⌛", "failed": "❌",
            }.get(b.status.value, "❓")
            lines.append(
                f"{status_emoji} #{b.id}: {b.booking_start.strftime('%Y-%m-%d %H:%M')} — "
                f"{b.booking_end.strftime('%H:%M')} [{b.status.value}]"
            )
        await message.answer("\n".join(lines))

    @dp.message(Command("cancel_booking"))
    async def cmd_cancel_booking(message: Message, command: CommandObject) -> None:
        session_factory: async_sessionmaker = message.bot.data.get("session_factory")
        if not session_factory:
            await message.answer("⚠️ База данных недоступна")
            return
        if not message.from_user:
            return

        args = (command.args or "").strip()
        if not args or not args.isdigit():
            await message.answer("Укажите ID брони: /cancel_booking <id>")
            return

        booking_id = int(args)
        async with session_factory() as session:
            user = await _get_or_create_user(session, message.from_user)
            settings = get_settings()
            is_admin = user.telegram_id in settings.telegram.admin_ids
            try:
                b = await bookings.cancel_booking(session, booking_id, user.id, is_admin)
                await session.commit()
                await message.answer(f"✅ Бронь #{b.id} отменена")
            except bookings.BookingNotFoundError:
                await message.answer(f"Бронь #{booking_id} не найдена")
            except bookings.BookingPermissionError as e:
                await message.answer(f"❌ {e}")

    @dp.message(Command("confirm_booking"))
    async def cmd_confirm_booking(message: Message, command: CommandObject) -> None:
        session_factory: async_sessionmaker = message.bot.data.get("session_factory")
        if not session_factory:
            await message.answer("⚠️ База данных недоступна")
            return

        args = (command.args or "").strip()
        if not args or not args.isdigit():
            await message.answer("Укажите ID брони: /confirm_booking <id>")
            return

        booking_id = int(args)
        async with session_factory() as session:
            try:
                b = await bookings.confirm_booking(session, booking_id)
                await session.commit()
                await message.answer(f"✅ Бронь #{b.id} подтверждена. Статус: CONFIRMED")
            except bookings.BookingNotFoundError:
                await message.answer(f"Бронь #{booking_id} не найдена")
            except bookings.BookingPermissionError as e:
                await message.answer(f"❌ {e}")

    @dp.message(Command("free_slots"))
    async def cmd_free_slots(message: Message) -> None:
        session_factory: async_sessionmaker = message.bot.data.get("session_factory")
        if not session_factory:
            await message.answer("⚠️ База данных недоступна")
            return

        from models import ChatTVBinding
        from sqlalchemy import select
        async with session_factory() as session:
            binding_q = select(ChatTVBinding).where(
                ChatTVBinding.chat_id == message.chat.id,
                ChatTVBinding.status == "active"
            )
            binding = (await session.execute(binding_q)).scalar_one_or_none()
            if not binding:
                await message.answer("❌ Чат не привязан к телевизору")
                return

            free = await bookings.list_free_slots(session, binding.tv_device_id)

        if not free:
            await message.answer("Свободных слотов в ближайшие 24 часа нет.")
            return

        lines = ["📺 Свободные интервалы (24ч):"]
        for start, end in free[:10]:
            lines.append(f"  {start.strftime('%Y-%m-%d %H:%M')} — {end.strftime('%H:%M')}")
        await message.answer("\n".join(lines))

    @dp.message(Command("resolve_overrun"))
    async def cmd_resolve_overrun(message: Message, command: CommandObject) -> None:
        session_factory: async_sessionmaker = message.bot.data.get("session_factory")
        if not session_factory:
            await message.answer("⚠️ База данных недоступна")
            return
        if not message.from_user:
            return

        settings = get_settings()
        if message.from_user.id not in settings.telegram.admin_ids:
            await message.answer("🚫 Только админ")
            return

        args = (command.args or "").strip()
        if not args or not args.isdigit():
            await message.answer("Укажите ID брони: /resolve_overrun <id>")
            return

        booking_id = int(args)
        async with session_factory() as session:
            try:
                b = await bookings.resolve_overrun(session, booking_id)
                await session.commit()
                await message.answer(f"✅ Overrun для брони #{b.id} разрешён. Продолжаем просмотр.")
            except bookings.BookingNotFoundError:
                await message.answer(f"Бронь #{booking_id} не найдена")
            except bookings.BookingPermissionError as e:
                await message.answer(f"❌ {e}")


__all__ = ["register"]