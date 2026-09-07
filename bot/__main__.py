"""Telegram bot entrypoint.

Run: aiogram 3.x polling — `python -m bot` (default).
Web:  `python -m bot --webhook` to use webhook mode (not implemented in Epic 0).
"""
from __future__ import annotations

import asyncio

from bot.factory import create_bot, create_dispatcher
from core.config import get_settings
from core.logging import bind_context, configure_logging, get_logger, set_correlation_id


async def main() -> None:
    settings = get_settings()
    configure_logging(
        level=settings.logging.level,
        json_output=settings.logging.json_output or settings.environment == "prod",
        include_timestamp=settings.logging.include_timestamp,
        service_name=settings.service_name,
    )
    log = get_logger("bot")
    log.info("bot_starting", mode=settings.telegram.bot_mode)

    bot = create_bot(settings.telegram)
    dp = create_dispatcher()

    # Register base middlewares (correlation_id, user binding)
    from bot.middlewares import CorrelationMiddleware, UserContextMiddleware
    dp.message.middleware(CorrelationMiddleware())
    dp.callback_query.middleware(CorrelationMiddleware())
    dp.message.middleware(UserContextMiddleware())
    dp.callback_query.middleware(UserContextMiddleware())

    # Register base handlers
    from bot.handlers import base as base_handlers
    from bot.handlers import bookings as booking_handlers
    from bot.handlers import voting as voting_handlers
    from bot.handlers import downloader as downloader_handlers
    from bot.handlers import notifications as notification_handlers
    from bot.handlers import observability as observability_handlers
    base_handlers.register(dp)
    booking_handlers.register(dp)
    voting_handlers.register(dp)
    downloader_handlers.register(dp)
    notification_handlers.register(dp)
    observability_handlers.register(dp)

    # Scheduler manager
    from services.scheduler import SchedulerManager
    from db.session import async_session_maker, create_session_factory, create_engine
    from core.config import get_settings
    settings = get_settings()
    scheduler_manager = SchedulerManager()
    await scheduler_manager.start()
    bot.data["scheduler_manager"] = scheduler_manager

    # Initialize global session factory for scheduler jobs
    engine = create_engine(settings.database)
    async_session_maker = create_session_factory(engine)

    try:
        if settings.telegram.bot_mode == "polling":
            await dp.start_polling(bot)
        else:
            log.warning("webhook_mode_not_implemented", fallback="polling")
            await dp.start_polling(bot)
    finally:
        if "scheduler_manager" in bot.data:
            await bot.data["scheduler_manager"].shutdown()
        await bot.session.close()
        log.info("bot_stopped")


if __name__ == "__main__":
    asyncio.run(main())
