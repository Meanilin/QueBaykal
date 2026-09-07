"""Telegram bot entrypoint.

Run: aiogram 3.x polling — `python -m bot` (default).
Web:  `python -m bot --webhook` to use webhook mode (not implemented in Epic 0).
"""
from __future__ import annotations

import asyncio

from bot.factory import create_bot, create_dispatcher
from core.config import get_settings
from core.logging import bind_context, configure_logging, get_logger, set_correlation_id
from db.session import create_engine, create_session_factory
from services.scheduler import SchedulerManager


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

    engine = create_engine(settings.database)
    session_factory = create_session_factory(engine)

    bot = create_bot(settings.telegram)
    # Share session_factory with handlers via bot.data
    bot.data["session_factory"] = session_factory

    dp = create_dispatcher()

    # Register base middlewares (correlation_id, user binding)
    from bot.middlewares import CorrelationMiddleware, UserContextMiddleware
    dp.message.middleware(CorrelationMiddleware())
    dp.callback_query.middleware(CorrelationMiddleware())
    dp.message.middleware(UserContextMiddleware())
    dp.callback_query.middleware(UserContextMiddleware())

    # Register handlers
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
    scheduler_manager = SchedulerManager(session_factory)
    await scheduler_manager.start()
    bot.data["scheduler_manager"] = scheduler_manager

    # Set global scheduler manager for scheduler jobs
    from services.scheduler import get_scheduler_manager
    import services.scheduler
    services.scheduler._scheduler_manager = scheduler_manager

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
        await engine.dispose()
        log.info("bot_stopped")


if __name__ == "__main__":
    asyncio.run(main())
