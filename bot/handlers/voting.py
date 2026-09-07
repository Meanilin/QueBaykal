"""Voting handlers for Telegram bot.

Issues #33-#49: /vote, suggestions, reactions, filter, final vote, winner.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReactionTypeEmoji,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Booking,
    BookingStatus,
    Chat,
    ChatMember,
    EventMovie,
    EventMovieStatus,
    TVDevice,
    User,
    VoteSession,
    VoteSessionState,
    VoteType,
)
from services import voting

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

router = Router(name="voting")


# --- Helpers ---

def _get_scheduler_manager(bot):
    return bot.data.get("scheduler_manager")


async def _get_or_create_user(session: AsyncSession, telegram_user) -> User:
    q = select(User).where(User.telegram_id == telegram_user.id)
    user = (await session.execute(q)).scalar_one_or_none()
    if not user:
        user = User(
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            full_name=telegram_user.full_name,
            language_code=telegram_user.language_code,
        )
        session.add(user)
        await session.flush()
    return user


async def _get_active_session(session: AsyncSession, chat_id: int) -> VoteSession | None:
    return await voting.get_active_session_for_chat(session, chat_id)


async def _ensure_admin_or_creator(
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    vote_session: VoteSession,
) -> bool:
    """Check if user is admin or creator."""
    if vote_session.created_by_user_id == user_id:
        return True
    q = select(ChatMember).where(
        ChatMember.chat_id == chat_id,
        ChatMember.user_id == user_id,
        ChatMember.role.in_(["admin", "root"]),
    )
    return (await session.execute(q)).scalar_one_or_none() is not None


def _format_movie_list(movies: list[EventMovie], show_votes: bool = True) -> str:
    if not movies:
        return "Предложений нет."
    lines = []
    for i, m in enumerate(movies, 1):
        suffix = ""
        if show_votes:
            suffix = f" — 🔥{m.reaction_count} | ✅{m.final_votes}"
        lines.append(f"{i}. {m.title} ({m.status.value}){suffix}")
    return "\n".join(lines)


def _format_session_status(vs: VoteSession) -> str:
    state_ru = {
        "created": "Создана",
        "suggestions": "Сбор предложений",
        "filter": "Отсев (Top-N)",
        "final_vote": "Финальное голосование",
        "winner_selected": "Победитель выбран",
        "downloading": "Скачивание",
        "ready": "Готово к просмотру",
        "playing": "Воспроизведение",
        "completed": "Завершено",
        "download_failed": "Ошибка скачивания",
        "cancelled": "Отменено",
    }
    s = state_ru.get(vs.state.value, vs.state.value)
    return f"🗳 Сессия #{vs.id} — {s}\nРежим: {vs.mode}\nНачата: {vs.started_at.strftime('%Y-%m-%d %H:%M')}"


# --- /vote command ---

@router.message(Command("vote"))
async def cmd_vote(message: Message, command: CommandObject, session: AsyncSession):
    """Start a new vote session.
    
    Usage:
    /vote instant [duration_min]          — instant vote
    /vote planned YYYY-MM-DD HH:MM [dur]  — scheduled vote
    /vote mixed [duration]                — both instant + planned
    """
    args = (command.args or "").strip().split()
    if not args:
        await message.answer(
            "Использование:\n"
            "/vote instant [мин_предложений] [мин_голосования] — мгновенное голосование\n"
            "/vote planned YYYY-MM-DD HH:MM [мин_предложений] [мин_голосования] — запланированное\n"
            "/vote mixed [мин_предложений] [мин_голосования] — смешанное"
        )
        return

    mode = args[0].lower()
    if mode not in ("instant", "planned", "mixed"):
        await message.answer("Режим должен быть: instant, planned или mixed")
        return

    chat_id = message.chat.id
    user = await _get_or_create_user(session, message.from_user)

    # Check for existing active session
    existing = await _get_active_session(session, chat_id)
    if existing:
        await message.answer(f"Уже есть активная сессия #{existing.id} ({existing.state.value}). Сначала завершите её.")
        return

    # Parse durations
    suggest_dur = int(args[1]) if len(args) > 1 else 10
    filter_dur = 5
    vote_dur = int(args[2]) if len(args) > 2 else 10

    if mode == "planned":
        if len(args) < 3:
            await message.answer("Для planned нужен: /vote planned YYYY-MM-DD HH:MM [suggest_dur] [vote_dur]")
            return
        try:
            scheduled_start = datetime.strptime(f"{args[1]} {args[2]}", "%Y-%m-%d %H:%M")
        except ValueError:
            await message.answer("Формат даты: YYYY-MM-DD HH:MM")
            return
        suggest_dur = int(args[3]) if len(args) > 3 else 10
        vote_dur = int(args[4]) if len(args) > 4 else 10
    else:
        scheduled_start = None

    # Get TV device for this chat
    q = select(TVDevice).join(Chat.tv_bindings).where(
        Chat.telegram_id == chat_id,
        ChatTVBinding.status == "active",
        TVDevice.status == "online",
    )
    tv = (await session.execute(q)).scalar_one_or_none()
    if not tv:
        await message.answer("Нет привязанного и онлайн ТВ. Сначала настройте /setup")
        return

    # Create booking for planned/mixed
    booking = None
    if mode in ("planned", "mixed") and scheduled_start:
        booking = Booking(
            chat_id=chat_id,
            tv_device_id=tv.id,
            mode=mode,
            status=BookingStatus.PENDING,
            booking_start=scheduled_start,
            booking_end=scheduled_start + timedelta(minutes=120),
            movie_duration_estimate=120,
            created_by_user_id=user.id,
        )
        session.add(booking)
        await session.flush()

    # Create vote session
    vs = await voting.create_vote_session(
        session,
        chat_id=chat_id,
        tv_device_id=tv.id,
        mode=mode,
        created_by_user_id=user.id,
        booking_id=booking.id if booking else None,
        movie_duration_estimate=120,
        suggest_duration=suggest_dur,
        filter_duration=filter_dur,
        vote_duration=vote_dur,
        min_votes=0,
        top_n=5,
        scheduled_start=scheduled_start,
    )

    # Transition to SUGGESTIONS
    await voting.transition_state(session, vs, VoteSessionState.SUGGESTIONS)

    # Schedule timer to end suggestions
    sm = _get_scheduler_manager(message.bot)
    if sm:
        from datetime import timezone
        run_at = datetime.now(timezone.utc) + timedelta(minutes=suggest_dur)
        await sm.schedule_once(
            f"end_suggest_{vs.id}",
            "services.voting:end_suggest_job",
            run_at,
            {"vote_session_id": vs.id},
        )

    await message.answer(
        f"✅ Голосование запущено! (сессия #{vs.id})\n"
        f"Режим: {mode}\n"
        f"Фаза: Сбор предложений ({suggest_dur} мин)\n"
        f"Напишите название фильма, чтобы предложить его.\n"
        f"Реакция 🔥 — голос за фильм."
    )


# --- Suggest movie (text message) ---

@router.message(F.text & ~F.text.startswith("/"))
async def on_suggest_movie(message: Message, session: AsyncSession):
    """Handle movie suggestion from plain text."""
    vs = await _get_active_session(session, message.chat.id)
    if not vs or vs.state != VoteSessionState.SUGGESTIONS:
        return  # Not in suggestion phase

    user = await _get_or_create_user(session, message.from_user)
    title = message.text.strip()

    if len(title) > 256:
        await message.answer("Слишком длинное название (макс. 256 символов)")
        return

    try:
        movie = await voting.suggest_movie(
            session,
            vote_session_id=vs.id,
            user_id=user.id,
            title=title,
        )
        await message.answer(
            f"✅ Предложено: «{movie.title}»\n"
            f"Статус: {movie.status.value}\n"
            f"Нажмите 🔥 под этим сообщением, чтобы проголосовать."
        )
    except voting.VotingError as e:
        await message.answer(str(e))


# --- Fire reaction handler ---

@router.message_reaction()
async def on_reaction(message_reaction, session: AsyncSession):
    """Handle 🔥 reaction on movie suggestion messages."""
    # Get the message that was reacted to
    # Note: Aiogram 3.x provides MessageReactionUpdated with old/new reactions
    pass  # Will implement via middleware or check manually


# For now, use a callback-based approach for reactions
# (Telegram Bot API doesn't easily give us message_reaction events for channel posts)
# Alternative: user replies with "🔥" or uses inline button


# --- Inline: Fire vote button ---

@router.callback_query(F.data.startswith("fire:"))
async def cb_fire_vote(callback: CallbackQuery, session: AsyncSession):
    """Handle 🔥 inline button click."""
    _, session_id_str, movie_id_str = callback.data.split(":")
    vote_session_id = int(session_id_str)
    movie_id = int(movie_id_str)

    user = await _get_or_create_user(session, callback.from_user)

    try:
        movie = await voting.add_fire_reaction(
            session,
            vote_session_id=vote_session_id,
            user_id=user.id,
            movie_id=movie_id,
        )
        await callback.answer(f"🔥 Голос за «{movie.title}» засчитан! (всего {movie.reaction_count})")
    except voting.VotingError as e:
        await callback.answer(str(e), show_alert=True)
    except voting.VotingNotFoundError:
        await callback.answer("Фильм не найден", show_alert=True)


@router.callback_query(F.data.startswith("unfire:"))
async def cb_unfire_vote(callback: CallbackQuery, session: AsyncSession):
    """Remove fire vote."""
    _, session_id_str, movie_id_str = callback.data.split(":")
    vote_session_id = int(session_id_str)
    movie_id = int(movie_id_str)

    user = await _get_or_create_user(session, callback.from_user)

    try:
        movie = await voting.remove_fire_reaction(
            session,
            vote_session_id=vote_session_id,
            user_id=user.id,
            movie_id=movie_id,
        )
        await callback.answer(f"🔥 Голос убран. Осталось: {movie.reaction_count}")
    except voting.VotingError as e:
        await callback.answer(str(e), show_alert=True)


# --- List movies ---

@router.message(Command("list"))
async def cmd_list(message: Message, session: AsyncSession):
    """List all movies in current session."""
    vs = await _get_active_session(session, message.chat.id)
    if not vs:
        await message.answer("Нет активной сессии.")
        return

    movies = await voting.list_movies(session, vs.id)
    await message.answer(
        f"{_format_session_status(vs)}\n\n"
        f"Фильмы:\n{_format_movie_list(movies)}"
    )


# --- Admin: end suggestions early ---

@router.message(Command("end_suggest"))
async def cmd_end_suggest(message: Message, session: AsyncSession):
    """Admin: end suggestion phase early."""
    vs = await _get_active_session(session, message.chat.id)
    if not vs or vs.state != VoteSessionState.SUGGESTIONS:
        await message.answer("Не в фазе предложений.")
        return

    user = await _get_or_create_user(session, message.from_user)
    is_admin = await _ensure_admin_or_creator(session, message.chat.id, user.id, vs)

    if not is_admin:
        await message.answer("Только создатель или админ.")
        return

    vs = await voting.end_suggest(session, vs.id)
    await message.answer(f"Фаза предложений завершена. Новое состояние: {vs.state.value}")

    # If FILTER, run it and schedule next phase
    if vs.state == VoteSessionState.FILTER:
        await run_filter_phase(session, message.bot, vs.id)


# --- Filter phase job ---

async def run_filter_phase(session: AsyncSession, bot, vote_session_id: int):
    """Run filter stage and transition to FINAL_VOTE."""
    finalists = await voting.run_filter_stage(session, vote_session_id)

    vs = await voting.get_vote_session(session, vote_session_id)
    await voting.transition_state(session, vs, VoteSessionState.FINAL_VOTE)

    # Send final vote keyboard to chat
    if finalists:
        kb = InlineKeyboardBuilder()
        for m in finalists:
            kb.button(text=f"✅ {m.title} ({m.reaction_count}🔥)", callback_data=f"final:{vs.id}:{m.id}")
        kb.adjust(1)

        chat_q = select(Chat.telegram_id).where(Chat.id == vs.chat_id)
        chat_tg_id = (await session.execute(chat_q)).scalar_one()

        await bot.send_message(
            chat_tg_id,
            f"🗳 Финальное голосование (сессия #{vs.id})!\n"
            f"Топ-{vs.top_n} фильмов. Выберите один:",
            reply_markup=kb.as_markup(),
        )

    # Schedule final vote end
    sm = _get_scheduler_manager(bot)
    if sm:
        from datetime import timezone
        run_at = datetime.now(timezone.utc) + timedelta(minutes=vs.vote_duration or 10)
        await sm.schedule_once(
            f"end_vote_{vs.id}",
            "services.voting:end_final_vote_job",
            run_at,
            {"vote_session_id": vs.id},
        )


# --- Final vote callback ---

@router.callback_query(F.data.startswith("final:"))
async def cb_final_vote(callback: CallbackQuery, session: AsyncSession):
    """Handle final vote button."""
    _, session_id_str, movie_id_str = callback.data.split(":")
    vote_session_id = int(session_id_str)
    movie_id = int(movie_id_str)

    user = await _get_or_create_user(session, callback.from_user)

    try:
        movie = await voting.cast_final_vote(
            session,
            vote_session_id=vote_session_id,
            user_id=user.id,
            movie_id=movie_id,
        )
        await callback.answer(f"✅ Голос за «{movie.title}» засчитан! (всего {movie.final_votes})")
    except voting.VotingError as e:
        await callback.answer(str(e), show_alert=True)


# --- End final vote job ---

async def end_final_vote_job(session: AsyncSession, bot, vote_session_id: int):
    """Job: end final vote, select winner."""
    vs = await voting.get_vote_session(session, vote_session_id)

    # Check min_votes
    min_ok = await voting.check_min_votes(session, vote_session_id)
    if not min_ok:
        await voting.transition_state(session, vs, VoteSessionState.CANCELLED)
        chat_q = select(Chat.telegram_id).where(Chat.id == vs.chat_id)
        chat_tg_id = (await session.execute(chat_q)).scalar_one()
        await bot.send_message(chat_tg_id, "❌ Не набралось минимальное число голосов. Голосование отменено.")
        return

    winner = await voting.select_winner(session, vote_session_id)
    await voting.transition_state(session, vs, VoteSessionState.WINNER_SELECTED)

    chat_q = select(Chat.telegram_id).where(Chat.id == vs.chat_id)
    chat_tg_id = (await session.execute(chat_q)).scalar_one()

    await bot.send_message(
        chat_tg_id,
        f"🏆 Победитель: «{winner.title}»!\n"
        f"Голосов: {winner.final_votes}\n"
        f"Статус: {winner.status.value}\n\n"
        f"Скачивание начнётся автоматически..."
    )

    # Transition to DOWNLOADING (Epic 3 will handle)
    await voting.transition_state(session, vs, VoteSessionState.DOWNLOADING)


# --- Admin: retry vote ---

@router.message(Command("retry_vote"))
async def cmd_retry_vote(message: Message, session: AsyncSession):
    """Retry vote after download failure or cancel."""
    vs = await _get_active_session(session, message.chat.id)
    if not vs or vs.state not in (VoteSessionState.DOWNLOAD_FAILED, VoteSessionState.CANCELLED):
        await message.answer("Повтор возможен только после ошибки скачивания или отмены.")
        return

    user = await _get_or_create_user(session, message.from_user)
    is_admin = await _ensure_admin_or_creator(session, message.chat.id, user.id, vs)

    if not is_admin:
        await message.answer("Только создатель или админ.")
        return

    vs = await voting.retry_vote(session, vs.id)
    await message.answer(f"Голосование перезапущено. Фаза: {vs.state.value}")


# --- Admin: cancel vote ---

@router.message(Command("cancel_vote"))
async def cmd_cancel_vote(message: Message, session: AsyncSession):
    """Cancel active vote session."""
    vs = await _get_active_session(session, message.chat.id)
    if not vs:
        await message.answer("Нет активной сессии.")
        return

    user = await _get_or_create_user(session, message.from_user)
    is_admin = await _ensure_admin_or_creator(session, message.chat.id, user.id, vs)

    if not is_admin:
        await message.answer("Только создатель или админ.")
        return

    await voting.cancel_session(session, vs.id, user.id, is_admin=True)
    await message.answer("❌ Голосование отменено.")


# --- Admin: force end vote ---

@router.message(Command("end_vote"))
async def cmd_end_vote(message: Message, session: AsyncSession):
    """Admin: force end vote (cancel)."""
    await cmd_cancel_vote(message, session)


# --- /set_top_n ---

@router.message(Command("set_top_n"))
async def cmd_set_top_n(message: Message, command: CommandObject, session: AsyncSession):
    """Set Top-N for filter (admin/creator)."""
    args = (command.args or "").strip()
    if not args.isdigit():
        await message.answer("Использование: /set_top_n <число 2-10>")
        return

    n = int(args)
    if not (2 <= n <= 10):
        await message.answer("N должен быть от 2 до 10")
        return

    vs = await _get_active_session(session, message.chat.id)
    if not vs:
        await message.answer("Нет активной сессии.")
        return

    user = await _get_or_create_user(session, message.from_user)
    is_admin = await _ensure_admin_or_creator(session, message.chat.id, user.id, vs)

    if not is_admin:
        await message.answer("Только создатель или админ.")
        return

    vs.top_n = n
    await session.flush()
    await message.answer(f"Top-N установлен: {n}")


# --- /vote_status ---

@router.message(Command("vote_status"))
async def cmd_vote_status(message: Message, session: AsyncSession):
    """Show current vote session status."""
    vs = await _get_active_session(session, message.chat.id)
    if not vs:
        await message.answer("Нет активной сессии.")
        return

    movies = await voting.list_movies(session, vs.id)
    await message.answer(f"{_format_session_status(vs)}\n\nФильмы:\n{_format_movie_list(movies)}")


# --- Helper to build movie list keyboard with fire buttons ---

async def _send_movie_list_with_buttons(bot, chat_id: int, session: AsyncSession, vs: VoteSession):
    """Send updated movie list with fire buttons."""
    movies = await voting.list_movies(session, vs.id, status_filter=[EventMovieStatus.PROPOSED, EventMovieStatus.FILTERED_IN])

    if not movies:
        await bot.send_message(chat_id, "Предложений пока нет.")
        return

    kb = InlineKeyboardBuilder()
    for m in movies:
        kb.button(text=f"🔥 {m.title} ({m.reaction_count})", callback_data=f"fire:{vs.id}:{m.id}")
    kb.adjust(1)

    await bot.send_message(
        chat_id,
        f"📋 Предложенные фильмы (сессия #{vs.id}):\nНажмите 🔥 чтобы проголосовать",
        reply_markup=kb.as_markup(),
    )


def register(dp):
    dp.include_router(router)