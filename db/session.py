"""Database engine, session factory, dependency injection."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config import DatabaseSettings


def create_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Build a singleton async engine for the application lifetime."""
    return create_async_engine(
        settings.to_dsn(driver="asyncpg"),
        echo=settings.echo,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout_seconds,
        pool_pre_ping=True,
        future=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Context manager for ad-hoc scripts and worker code."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield a session, commit on success, rollback on error."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


__all__ = [
    "create_engine",
    "create_session_factory",
    "get_session",
    "session_scope",
    "async_session_maker",  # global factory for workers/scheduler
]


# Global session factory (initialized in bot/__main__.py or api/__main__.py)
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
async_session_maker: async_sessionmaker[AsyncSession] | None = None
