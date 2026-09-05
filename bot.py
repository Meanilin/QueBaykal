"""Telegram-бот для очереди на совместный просмотр кино."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Iterable

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS: set[int] = {
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(",", " ").split() if x.strip().isdigit()
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("cinema-queue-bot")


# --------------------------------------------------------------------------- #
# Хранилище (in-memory; для продакшна подключить Redis/Postgres)
# --------------------------------------------------------------------------- #
@dataclass
class Movie:
    movie_id: int
    title: str
    added_by: int
    voters: set[int] = field(default_factory=set)
    watchers: list[int] = field(default_factory=list)  # итоговая очередь просмотра

    @property
    def votes(self) -> int:
        return len(self.voters)


class MovieStore:
    def __init__(self) -> None:
        self._movies: dict[int, Movie] = {}
        self._next_id: int = 1
        self._lock = asyncio.Lock()

    async def create(self, title: str, added_by: int) -> Movie:
        async with self._lock:
            mid = self._next_id
            self._next_id += 1
            m = Movie(movie_id=mid, title=title.strip(), added_by=added_by)
            self._movies[mid] = m
            return m

    async def get(self, movie_id: int) -> Movie | None:
        return self._movies.get(movie_id)

    async def all_sorted(self) -> list[Movie]:
        return sorted(self._movies.values(), key=lambda m: (-m.votes, m.movie_id))

    async def delete(self, movie_id: int, user_id: int, is_admin: bool) -> bool:
        m = self._movies.get(movie_id)
        if m is None:
            return False
        if not is_admin and m.added_by != user_id:
            return False
        del self._movies[movie_id]
        return True

    async def toggle_vote(self, movie_id: int, user_id: int) -> Movie | None:
        m = self._movies.get(movie_id)
        if m is None:
            return None
        if user_id in m.voters:
            m.voters.remove(user_id)
        else:
            m.voters.add(user_id)
        return m

    async def build_queue(self, movie_id: int) -> Movie | None:
        m = self._movies.get(movie_id)
        if m is None:
            return None
        m.watchers = sorted(m.voters)
        return m


store = MovieStore()


# --------------------------------------------------------------------------- #
# FSM: диалог добавления фильма
# --------------------------------------------------------------------------- #
class AddMovie(StatesGroup):
    waiting_title = State()


# --------------------------------------------------------------------------- #
# Клавиатуры
# --------------------------------------------------------------------------- #
def movies_kb(movies: Iterable[Movie], user_id: int, page: int = 0, per_page: int = 5) -> InlineKeyboardMarkup:
    items = list(movies)
    start = page * per_page
    chunk = items[start : start + per_page]
    rows: list[list[InlineKeyboardButton]] = []
    for m in chunk:
        voted = "🔴" if user_id in m.voters else "⚪"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{voted} #{m.movie_id} · {m.title}",
                    callback_data=f"noop:{m.movie_id}",
                ),
                InlineKeyboardButton(
                    text=f"👍 {m.votes}",
                    callback_data=f"vote:{m.movie_id}",
                ),
                InlineKeyboardButton(text="🗑", callback_data=f"del:{m.movie_id}"),
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if start > 0:
        nav.append(InlineKeyboardButton(text="◀", callback_data=f"page:{page - 1}"))
    if start + per_page < len(items):
        nav.append(InlineKeyboardButton(text="▶", callback_data=f"page:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="➕ Добавить фильм", callback_data="add")])
    rows.append([InlineKeyboardButton(text="📋 Сформировать очередь", callback_data="queue")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_movies(movies: list[Movie], page: int, per_page: int = 5) -> str:
    if not movies:
        return "🎬 Список пуст. Нажмите «➕ Добавить фильм»."
    start = page * per_page
    end = start + per_page
    chunk = movies[start:end]
    header = f"🎬 Фильмы (сортировка: по голосам)\nСтр. {page + 1}/{(len(movies) - 1) // per_page + 1}\n"
    lines = [header]
    for i, m in enumerate(chunk, start=start + 1):
        lines.append(f"{i}. #{m.movie_id} {m.title} — 👍 {m.votes}")
    return "\n".join(lines)


def format_queue(movies: list[Movie]) -> str:
    if not movies:
        return "Очередь пуста. Добавьте фильмы и соберите голоса."
    parts = ["📋 Очередь на просмотр (по голосам):\n"]
    for i, m in enumerate(movies, start=1):
        parts.append(f"{i}. {m.title} — 👍 {m.votes}")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Роутер
# --------------------------------------------------------------------------- #
router = Router()
PAGE = {}


@router.message(Command("start", "help"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "🎬 *Бот-очередь на кино*\n\n"
        "/movies — список фильмов и голосование\n"
        "/add — предложить фильм\n"
        "/queue — итоговая очередь\n"
        "/cancel — отменить ввод",
        parse_mode="Markdown",
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state():
        await state.clear()
        await message.answer("Отменил.")
    else:
        await message.answer("Нечего отменять.")


@router.message(Command("add"))
@router.callback_query(F.data == "add")
async def add_start(event: Message | CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddMovie.waiting_title)
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer("Введите название фильма:")


@router.message(AddMovie.waiting_title)
async def add_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title:
        await message.answer("Название не может быть пустым. Введите ещё раз или /cancel.")
        return
    if len(title) > 120:
        await message.answer("Слишком длинное название (>120). Сократите.")
        return
    m = await store.create(title=title, added_by=message.from_user.id)  # type: ignore[union-attr]
    await state.clear()
    await message.answer(f"✅ Добавлено: #{m.movie_id} {m.title}\n/movies — проголосовать.")


@router.message(Command("movies"))
async def cmd_movies(message: Message) -> None:
    movies = await store.all_sorted()
    PAGE[message.from_user.id] = 0  # type: ignore[index]
    await message.answer(
        format_movies(movies, page=0),
        reply_markup=movies_kb(movies, message.from_user.id, page=0),  # type: ignore[arg-type]
    )


@router.message(Command("queue"))
async def cmd_queue(message: Message) -> None:
    movies = await store.all_sorted()
    # фиксируем порядок просмотра: топ-голосов -> идут первыми
    await store.build_queue(movies[0].movie_id) if movies else None
    text = format_queue(movies)
    if movies:
        top = movies[0]
        text += f"\n\nСейчас смотрим: *{top.title}* (👍 {top.votes})"
    await message.answer(text, parse_mode="Markdown")


@router.callback_query(F.data.startswith("vote:"))
async def cb_vote(call: CallbackQuery) -> None:
    mid = int(call.data.split(":", 1)[1])
    m = await store.toggle_vote(mid, call.from_user.id)
    if m is None:
        await call.answer("Фильм не найден", show_alert=True)
        return
    await call.answer(f"Голосов: {m.votes}")
    await refresh_movies(call)


@router.callback_query(F.data.startswith("del:"))
async def cb_delete(call: CallbackQuery) -> None:
    mid = int(call.data.split(":", 1)[1])
    is_admin = call.from_user.id in ADMIN_IDS
    ok = await store.delete(mid, call.from_user.id, is_admin)
    if not ok:
        await call.answer("Недоступно", show_alert=True)
        return
    await call.answer("Удалено")
    await refresh_movies(call)


@router.callback_query(F.data.startswith("page:"))
async def cb_page(call: CallbackQuery) -> None:
    page = int(call.data.split(":", 1)[1])
    PAGE[call.from_user.id] = page
    await refresh_movies(call)


@router.callback_query(F.data == "queue")
async def cb_queue(call: CallbackQuery) -> None:
    movies = await store.all_sorted()
    text = format_queue(movies)
    if movies:
        await store.build_queue(movies[0].movie_id)
        top = movies[0]
        text += f"\n\nСейчас смотрим: *{top.title}* (👍 {top.votes})"
    await call.message.answer(text, parse_mode="Markdown")
    await call.answer()


async def refresh_movies(call: CallbackQuery) -> None:
    movies = await store.all_sorted()
    page = PAGE.get(call.from_user.id, 0)
    try:
        await call.message.edit_text(
            format_movies(movies, page=page),
            reply_markup=movies_kb(movies, call.from_user.id, page=page),
        )
    except Exception:  # текст не изменился
        pass


# --------------------------------------------------------------------------- #
# Запуск
# --------------------------------------------------------------------------- #
async def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "your_telegram_bot_token_here":
        raise SystemExit(
            "Не задан BOT_TOKEN. Скопируйте .env.example в .env и впишите токен @BotFather."
        )
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    log.info("Старт бота")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
