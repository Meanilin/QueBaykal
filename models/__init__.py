"""SQLAlchemy ORM models (async) — full voting models.

Issue #31: VoteSession, EventMovie, Vote models with all states.
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
    PENDING = "pending"
    CONFIRMED = "confirmed"
    ACTIVE = "active"
    OVERRUN = "overrun"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


class VoteSessionState(str, enum.Enum):
    """State machine for a vote session.

    Flow:
    CREATED → SUGGESTIONS → FILTER → FINAL_VOTE → WINNER_SELECTED
    → DOWNLOADING → READY → PLAYING → COMPLETED
    
    Error paths:
    → DOWNLOAD_FAILED → (retry via /provide_link or /retry_vote)
    → CANCELLED (admin/creator stop)
    """

    CREATED = "created"
    SUGGESTIONS = "suggestions"
    FILTER = "filter"
    FINAL_VOTE = "final_vote"
    WINNER_SELECTED = "winner_selected"
    DOWNLOADING = "downloading"
    READY = "ready"
    PLAYING = "playing"
    COMPLETED = "completed"
    DOWNLOAD_FAILED = "download_failed"
    CANCELLED = "cancelled"


class EventMovieStatus(str, enum.Enum):
    """Status of a movie proposal within a vote session."""

    PROPOSED = "proposed"
    FILTERED_IN = "filtered_in"
    FILTERED_OUT = "filtered_out"
    FINALIST = "finalist"
    WINNER = "winner"


class VoteType(str, enum.Enum):
    FIRE = "fire"          # 🔥 reaction
    FINAL = "final"        # inline button vote


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
    vote_sessions_created: Mapped[list["VoteSession"]] = relationship(
        back_populates="creator", foreign_keys="VoteSession.created_by_user_id"
    )
    movie_proposals: Mapped[list["EventMovie"]] = relationship(
        back_populates="proposer", foreign_keys="EventMovie.submitted_by_user_id"
    )
    votes: Mapped[list["Vote"]] = relationship(
        back_populates="voter", foreign_keys="Vote.user_id"
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
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
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
    movie_duration_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    __tablename__ = "scheduled_jobs"
    __table_args__ = (
        Index("ix_scheduled_jobs_next_run", "next_run_time"),
        Index("ix_scheduled_jobs_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    apscheduler_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    callback: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    args: Mapped[str | None] = mapped_column(Text, nullable=True)
    kwargs: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class VoteSession(Base):
    """A voting session for selecting a movie.

    Can be instant (same-session) or planned (scheduled).
    """

    __tablename__ = "vote_sessions"
    __table_args__ = (
        Index("ix_vote_sessions_chat_state", "chat_id", "state"),
        Index("ix_vote_sessions_scheduled", "scheduled_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    tv_device_id: Mapped[int] = mapped_column(
        ForeignKey("tv_devices.id", ondelete="CASCADE"), nullable=False
    )
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # instant | planned | mixed
    state: Mapped[VoteSessionState] = mapped_column(
        Enum(
            VoteSessionState,
            name="vote_session_state",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=VoteSessionState.CREATED,
        server_default=VoteSessionState.CREATED.value,
    )
    suggest_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filter_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vote_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    movie_duration_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    winner_movie_id: Mapped[int | None] = mapped_column(
        ForeignKey("event_movies.id", ondelete="SET NULL"), nullable=True
    )
    min_votes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    top_n: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
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
    movies: Mapped[list["EventMovie"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    votes: Mapped[list["Vote"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    creator: Mapped[User] = relationship(back_populates="vote_sessions_created")


class EventMovie(Base):
    """A movie proposed during a vote session."""

    __tablename__ = "event_movies"
    __table_args__ = (
        Index("ix_event_movies_session_reaction", "session_id", "reaction_count"),
        Index("ix_event_movies_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("vote_sessions.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    submitted_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[EventMovieStatus] = mapped_column(
        Enum(
            EventMovieStatus,
            name="event_movie_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=EventMovieStatus.PROPOSED,
        server_default=EventMovieStatus.PROPOSED.value,
    )
    reaction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    final_votes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    media_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_files.id", ondelete="SET NULL"), nullable=True
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

    session: Mapped[VoteSession] = relationship(back_populates="movies")
    proposer: Mapped[User] = relationship(back_populates="movie_proposals")
    fire_votes: Mapped[list["Vote"]] = relationship(
        back_populates="movie", foreign_keys="Vote.movie_id",
        primaryjoin="and_(Vote.movie_id==EventMovie.id, Vote.vote_type=='fire')"
    )
    final_votes_rel: Mapped[list["Vote"]] = relationship(
        back_populates="movie", foreign_keys="Vote.movie_id",
        primaryjoin="and_(Vote.movie_id==EventMovie.id, Vote.vote_type=='final')"
    )


class Vote(Base):
    """A vote (fire reaction or final inline vote)."""

    __tablename__ = "votes"
    __table_args__ = (
        # One vote per user per session per vote type
        UniqueConstraint("session_id", "user_id", "vote_type", "movie_id", name="uq_vote_session_user_type_movie"),
        Index("ix_votes_session_type", "session_id", "vote_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("vote_sessions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    movie_id: Mapped[int | None] = mapped_column(
        ForeignKey("event_movies.id", ondelete="CASCADE"), nullable=True
    )
    vote_type: Mapped[VoteType] = mapped_column(
        Enum(
            VoteType,
            name="vote_type",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[VoteSession] = relationship(back_populates="votes")
    voter: Mapped[User] = relationship(back_populates="votes")
    movie: Mapped[EventMovie | None] = relationship(back_populates="fire_votes", foreign_keys=[movie_id])


# VoteSessionState stub for models already imported above

__all__ = [
    "Base",
    "Booking",
    "BookingStatus",
    "Chat",
    "ChatMember",
    "ChatTVBinding",
    "ChatTVBindingStatus",
    "EventMovie",
    "EventMovieStatus",
    "MediaFile",
    "ScheduledJob",
    "TVDevice",
    "TVDeviceStatus",
    "User",
    "UserRole",
    "Vote",
    "VoteSession",
    "VoteSessionState",
    "VoteType",
]


class MediaFile(Base):
    """Media file metadata — from Epic 3 (Downloader). Stub for FKs."""

    __tablename__ = "media_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_url_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    local_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(256), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_tracks: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    subtitle_tracks: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )