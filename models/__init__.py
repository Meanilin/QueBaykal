"""SQLAlchemy ORM models (async).

Issue #12: foundational models for users, chats, chat_members, tv_devices, chat_tv_bindings.
All FKs cascade carefully. Time stamps in UTC.
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
    """Lifecycle of a TV device registration."""

    PENDING = "pending"        # awaiting first agent heartbeat
    ONLINE = "online"
    OFFLINE = "offline"
    DISABLED = "disabled"      # manually disabled by admin


class ChatTVBindingStatus(str, enum.Enum):
    """Whether a chat is actively bound to a TV."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class UserRole(str, enum.Enum):
    """Per-chat role of a user."""

    MEMBER = "member"
    ADMIN = "admin"
    ROOT = "root"  # global admin, defined by TG_ADMIN_IDS


# --- Models ---

class User(Base):
    """Telegram user known to the system.

    Created on first interaction. `telegram_id` is the source of truth.
    """

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


class Chat(Base):
    """Telegram chat (group, supergroup, or private)."""

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    chat_type: Mapped[str] = mapped_column(String(16), nullable=False)  # group, supergroup, private
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # chat-scoped settings
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


class ChatMember(Base):
    """User membership in a chat with a role."""

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
    """Physical or virtual TV device managed by an agent."""

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
    """M2M relation: a chat can control one or more TVs.

    Most chats will have exactly one ACTIVE binding, but a chat may be
    temporarily unbound (e.g. switching TVs).
    """

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


__all__ = [
    "Base",
    "Chat",
    "ChatMember",
    "ChatTVBinding",
    "ChatTVBindingStatus",
    "TVDevice",
    "TVDeviceStatus",
    "User",
    "UserRole",
]
