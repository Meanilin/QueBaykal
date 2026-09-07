"""Structured logging with correlation IDs.

Implements issue #16: structlog + correlation_id middleware.
Each log record carries:
- timestamp
- level
- service name
- correlation_id (per request/job/command)
- user_id, chat_id (where applicable)
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.types import EventDict, Processor

# Per-task correlation ID. Set by middleware, read by loggers.
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_user_id: ContextVar[int | None] = ContextVar("user_id", default=None)
_chat_id: ContextVar[int | None] = ContextVar("chat_id", default=None)


def set_correlation_id(value: str | None = None) -> str:
    cid = value or uuid.uuid4().hex[:16]
    _correlation_id.set(cid)
    return cid


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def bind_context(*, user_id: int | None = None, chat_id: int | None = None) -> None:
    if user_id is not None:
        _user_id.set(user_id)
    if chat_id is not None:
        _chat_id.set(chat_id)


def clear_context() -> None:
    _correlation_id.set(None)
    _user_id.set(None)
    _chat_id.set(None)


def _add_context_processor(_: Any, __: str, event_dict: EventDict) -> EventDict:
    cid = _correlation_id.get()
    if cid:
        event_dict.setdefault("correlation_id", cid)
    uid = _user_id.get()
    if uid is not None:
        event_dict.setdefault("user_id", uid)
    cid_chat = _chat_id.get()
    if cid_chat is not None:
        event_dict.setdefault("chat_id", cid_chat)
    return event_dict


def configure_logging(
    level: str = "INFO",
    json_output: bool = False,
    include_timestamp: bool = True,
    service_name: str = "cinema-queue-bot",
) -> None:
    """Initialize structlog + stdlib logging.

    Called once at process startup from both bot and API.
    """
    timestamper = (
        structlog.processors.TimeStamper(fmt="iso", utc=True)
        if include_timestamp
        else (lambda _, __, d: d)
    )

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        _add_context_processor,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        # Production: JSON lines for log aggregation.
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        # Development: human-readable colored output.
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging (uvicorn, aiogram, sqlalchemy) to structlog.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    for noisy in ("aiogram.event", "aiogram.middlewares", "sqlalchemy.engine.Engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger.

    Usage:
        log = get_logger(__name__)
        log.info("event", key="value")
    """
    return structlog.get_logger(name) if name else structlog.get_logger()


__all__ = [
    "bind_context",
    "clear_context",
    "configure_logging",
    "get_correlation_id",
    "get_logger",
    "set_correlation_id",
]
