"""SQLAlchemy ORM models (async) — extended with Booking models.

Issue #19: bookings table with overrun status, APScheduler scheduled_jobs.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

if TYPE_CHECKING:
    pass


class Base(DeclarativeBase):
    """Base for all ORM models."""
    pass


# --- Enums ---

class TVDeviceStatus(str, enum.Enum):
    PENDING = "pending"
    ONLINE = "online"
    OFFLINE = "offline"
    DISABLED = "disabled"


class ChatTVBindingStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class UserRole(str, enum.Enum):
    MEMBER = "member"
    ADMIN = "admin"
    ROOT = "root"


class BookingStatus(str, enum.Enum):
    """Lifecycle of a TV booking.

    PENDING    -> created, awaiting /confirm_booking (1h before start)
    CONFIRMED  -> confirmed by user, waiting for start time
    ACTIVE     -> currently watching
    OVERRUN    -> actual duration exceeded, overlaps next booking
    COMPLETED  -> finished normally
    CANCELLED  -> cancelled by initiator/admin
    EXPIRED    -> auto-expired (no confirmation in time)
    FAILED     -> download/playback failed
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    ACTIVE = "active"
    OVERRUN = "overrun"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


# --- Models ---

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    memberships: Mapped[list["ChatMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    bookings: Mapped[list["Booking"]] = relationship(
        back_populates="creator", foreign_keys="Booking.created_by_user_id"
    )


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    chat_type: Mapped[str] = mapped_column(String(16), nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    members: Mapped[list["ChatMember"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    tv_bindings: Mapped[list["ChatTVBinding"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    bookings: Mapped[list["Booking"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    vote_sessions: Mapped[list["VoteSession"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )


class ChatMember(Base):
    __tablename__ = "chat_members"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_chat_members_chat_user"),
        Index("ix_chat_members_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=UserRole.MEMBER,
        server_default=UserRole.MEMBER.value,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    chat: Mapped[Chat] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class TVDevice(Base):
    __tablename__ = "tv_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(128), nullable=True)
    agent_token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[TVDeviceStatus] = mapped_column(
        Enum(
            TVDeviceStatus,
            name="tv_device_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=TVDeviceStatus.PENDING,
        server_default=TVDeviceStatus.PENDING.value,
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    bindings: Mapped[list["ChatTVBinding"]] = relationship(
        back_populates="tv_device", cascade="all, delete-orphan"
    )


class ChatTVBinding(Base):
    __tablename__ = "chat_tv_bindings"
    __table_args__ = (
        Index("ix_chat_tv_bindings_chat_active", "chat_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    tv_device_id: Mapped[int] = mapped_column(
        ForeignKey("tv_devices.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[ChatTVBindingStatus] = mapped_column(
        Enum(
            ChatTVBindingStatus,
            name="chat_tv_binding_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=ChatTVBindingStatus.ACTIVE,
        server_default=ChatTVBindingStatus.ACTIVE.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    chat: Mapped[Chat] = relationship(back_populates="tv_bindings")
    tv_device: Mapped[TVDevice] = relationship(back_populates="bindings")


class Booking(Base):
    """TV booking created by a user.

    One booking = one time slot on one TV device.
    """

    __tablename__ = "bookings"
    __table_args__ = (
        Index("ix_bookings_tv_start", "tv_device_id", "booking_start"),
        Index("ix_bookings_chat_start", "chat_id", "booking_start"),
        Index("ix_bookings_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    tv_device_id: Mapped[int] = mapped_column(
        ForeignKey("tv_devices.id", ondelete="CASCADE"), nullable=False
    )
    vote_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("vote_sessions.id", ondelete="SET NULL"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # instant | planned | mixed
    status: Mapped[BookingStatus] = mapped_column(
        Enum(
            BookingStatus,
            name="booking_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=BookingStatus.PENDING,
        server_default=BookingStatus.PENDING.value,
    )
    booking_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    booking_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    movie_duration_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)  # minutes
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_movie_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    chat: Mapped[Chat] = relationship(back_populates="bookings")
    tv_device: Mapped[TVDevice] = relationship()
    creator: Mapped[User] = relationship(back_populates="bookings")
    vote_session: Mapped["VoteSession | None"] = relationship(back_populates="booking")


class ScheduledJob(Base):
    """APScheduler jobs persisted in DB for recovery after restart.

    Mirrors apscheduler's job table with additional metadata.
    """

    __tablename__ = "scheduled_jobs"
    __table_args__ = (
        Index("ix_scheduled_jobs_next_run", "next_run_time"),
        Index("ix_scheduled_jobs_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    apscheduler_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    callback: Mapped[str] = mapped_column(Text, nullable=False)  # dotted path: module:function
    trigger: Mapped[str] = mapped_column(Text, nullable=False)   # JSON-serialized trigger
    args: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    kwargs: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON object
    next_run_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    misfire_grace_time: Mapped[int] = mapped_column(Integer, nullable=False, default=300, server_default="300")
    max_instances: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# VoteSession stub for FK (full model in Epic 2)
class VoteSession(Base):
    __tablename__ = "vote_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), nullable=False)
    tv_device_id: Mapped[int] = mapped_column(ForeignKey("tv_devices.id", ondelete="CASCADE"), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    movie_duration_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    chat: Mapped[Chat] = relationship(back_populates="vote_sessions")
    booking: Mapped[Booking | None] = relationship(back_populates="vote_session")


__all__ = [
    "Base",
    "Booking",
    "BookingStatus",
    "Chat",
    "ChatMember",
    "ChatTVBinding",
    "ChatTVBindingStatus",
    "ScheduledJob",
    "TVDevice",
    "TVDeviceStatus",
    "User",
    "UserRole",
    "VoteSession",
]