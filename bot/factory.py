"""Bot, dispatcher, and FSM storage factory.

Issue #14: aiogram 3.x Dispatcher wired with Redis FSM storage.
Issue #15: RedisStorage for persistent FSM state and FSM caches.

The bot is constructed in `create_bot()` and the dispatcher in `create_dispatcher()`.
Both are wired in `bot.__main__:main` and shared with the API lifespan when needed.
"""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from core.config import TelegramSettings, get_settings


def _parse_mode(value: str) -> ParseMode:
    return {
        "HTML": ParseMode.HTML,
        "Markdown": ParseMode.MARKDOWN,
        "MarkdownV2": ParseMode.MARKDOWN_V2,
    }[value]


def create_bot(settings: TelegramSettings | None = None) -> Bot:
    s = settings or get_settings().telegram
    return Bot(
        token=s.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=_parse_mode(s.parse_mode)),
    )


def create_storage(settings=None) -> BaseStorage:
    """Return Redis storage if available, else in-memory.

    The choice happens at startup: if Redis is reachable, use it; otherwise fall
    back to MemoryStorage. Both satisfy BaseStorage, so all dispatchers work.
    """
    s = (settings or get_settings()).redis
    try:
        redis = Redis(
            host=s.host,
            port=s.port,
            db=s.db,
            password=s.password.get_secret_value() if s.password else None,
        )
        return RedisStorage(redis=redis, state_ttl=s.fsm_ttl_seconds, data_ttl=s.fsm_ttl_seconds)
    except Exception:
        return MemoryStorage()


def create_dispatcher(storage: BaseStorage | None = None) -> Dispatcher:
    """Build dispatcher with shared storage.

    Routers and middlewares are registered by `register_routes()`.
    """
    if storage is None:
        storage = create_storage()
    return Dispatcher(storage=storage)


__all__ = [
    "create_bot",
    "create_dispatcher",
    "create_storage",
]
