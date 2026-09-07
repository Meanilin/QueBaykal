"""Booking service layer.

Issue #20, #21, #22, #23: CRUD operations for bookings, conflict detection,
confirmation flow, and listing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Booking, BookingStatus, Chat, TVDevice, User

if TYPE_CHECKING:
    pass


class BookingConflictError(Exception):
    """Raised when booking overlaps with an existing active/confirmed booking."""

    def __init__(self, existing: Booking, requested_start: datetime, requested_end: datetime):
        self.existing = existing
        self.requested_start = requested_start
        self.requested_end = requested_end
        super().__init__(
            f"Conflict with booking #{existing.id} "
            f"({existing.booking_start.isoformat()}–{existing.booking_end.isoformat()})"
        )


class BookingNotFoundError(Exception):
    pass


class BookingPermissionError(Exception):
    pass


async def create_booking(
    session: AsyncSession,
    *,
    chat_id: int,
    tv_device_id: int,
    booking_start: datetime,
    booking_end: datetime,
    created_by_user_id: int,
    mode: str = "instant",
    vote_session_id: int | None = None,
    movie_duration_estimate: int | None = None,
) -> Booking:
    """Create a new booking, checking for conflicts.

    Conflict rule: no overlap with existing bookings in ACTIVE or CONFIRMED
    status on the same TV device. PENDING bookings are not yet confirmed,
    but they still reserve the slot.
    """
    # Check TV exists
    tv = await session.get(TVDevice, tv_device_id)
    if not tv:
        raise ValueError(f"TV device {tv_device_id} not found")

    # Check chat exists
    chat = await session.get(Chat, chat_id)
    if not chat:
        raise ValueError(f"Chat {chat_id} not found")

    # Check user exists
    user = await session.get(User, created_by_user_id)
    if not user:
        raise ValueError(f"User {created_by_user_id} not found")

    # Conflict detection
    # Overlap condition: (start1 < end2) AND (start2 < end1)
    conflict_q = select(Booking).where(
        Booking.tv_device_id == tv_device_id,
        Booking.status.in_([BookingStatus.ACTIVE, BookingStatus.CONFIRMED, BookingStatus.PENDING]),
        and_(
            Booking.booking_start < booking_end,
            Booking.booking_end > booking_start,
        ),
    )
    existing = (await session.execute(conflict_q)).scalar_one_or_none()
    if existing:
        raise BookingConflictError(existing, booking_start, booking_end)

    booking = Booking(
        chat_id=chat_id,
        tv_device_id=tv_device_id,
        vote_session_id=vote_session_id,
        mode=mode,
        status=BookingStatus.PENDING,
        booking_start=booking_start,
        booking_end=booking_end,
        movie_duration_estimate=movie_duration_estimate,
        created_by_user_id=created_by_user_id,
    )
    session.add(booking)
    await session.flush()
    return booking


async def get_booking(session: AsyncSession, booking_id: int) -> Booking:
    booking = await session.get(Booking, booking_id)
    if not booking:
        raise BookingNotFoundError(f"Booking {booking_id} not found")
    return booking


async def cancel_booking(
    session: AsyncSession,
    booking_id: int,
    user_id: int,
    is_admin: bool = False,
) -> Booking:
    booking = await get_booking(session, booking_id)

    # Only creator or admin can cancel
    if booking.created_by_user_id != user_id and not is_admin:
        raise BookingPermissionError("Only booking creator or admin can cancel")

    if booking.status in [BookingStatus.COMPLETED, BookingStatus.CANCELLED, BookingStatus.EXPIRED]:
        raise BookingPermissionError(f"Cannot cancel booking in status {booking.status.value}")

    booking.status = BookingStatus.CANCELLED
    booking.cancellation_reason = f"Cancelled by user {user_id}"
    await session.flush()
    return booking


async def confirm_booking(session: AsyncSession, booking_id: int) -> Booking:
    """Confirm a pending booking (user clicked /confirm_booking)."""
    booking = await get_booking(session, booking_id)

    if booking.status != BookingStatus.PENDING:
        raise BookingPermissionError(f"Booking already {booking.status.value}")

    booking.status = BookingStatus.CONFIRMED
    booking.confirmed_at = datetime.now(booking.booking_start.tzinfo or datetime.utcnow().tzinfo)
    await session.flush()
    return booking


async def list_my_bookings(
    session: AsyncSession,
    user_id: int,
    *,
    status_filter: list[BookingStatus] | None = None,
    limit: int = 20,
) -> list[Booking]:
    q = select(Booking).where(Booking.created_by_user_id == user_id)
    if status_filter:
        q = q.where(Booking.status.in_(status_filter))
    q = q.order_by(Booking.booking_start.desc()).limit(limit)
    return list((await session.execute(q)).scalars().all())


async def list_free_slots(
    session: AsyncSession,
    tv_device_id: int,
    *,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
) -> list[tuple[datetime, datetime]]:
    """Return free time slots on a TV device within [from_dt, to_dt].

    Uses existing bookings with status PENDING/CONFIRMED/ACTIVE as blocked.
    Returns list of (start, end) tuples for free intervals.
    """
    if from_dt is None:
        from_dt = datetime.now()
    if to_dt is None:
        to_dt = from_dt + timedelta(hours=24)

    q = select(Booking).where(
        Booking.tv_device_id == tv_device_id,
        Booking.status.in_([BookingStatus.PENDING, BookingStatus.CONFIRMED, BookingStatus.ACTIVE]),
        Booking.booking_end > from_dt,
        Booking.booking_start < to_dt,
    ).order_by(Booking.booking_start)

    bookings = list((await session.execute(q)).scalars().all())

    # Build free slots
    free: list[tuple[datetime, datetime]] = []
    cursor = from_dt
    for b in bookings:
        if b.booking_start > cursor:
            free.append((cursor, min(b.booking_start, to_dt)))
        cursor = max(cursor, b.booking_end)
    if cursor < to_dt:
        free.append((cursor, to_dt))

    return free


async def expire_pending_bookings(session: AsyncSession, before: datetime) -> list[Booking]:
    """Mark PENDING bookings as EXPIRED if not confirmed 30 min before start."""
    q = select(Booking).where(
        Booking.status == BookingStatus.PENDING,
        Booking.booking_start <= before,
    )
    bookings = list((await session.execute(q)).scalars().all())
    for b in bookings:
        b.status = BookingStatus.EXPIRED
        b.cancellation_reason = "Auto-expired: no confirmation 30 min before start"
    return bookings


async def activate_booking(session: AsyncSession, booking_id: int) -> Booking:
    booking = await get_booking(session, booking_id)
    if booking.status != BookingStatus.CONFIRMED:
        raise BookingPermissionError(f"Cannot activate booking in status {booking.status.value}")
    booking.status = BookingStatus.ACTIVE
    await session.flush()
    return booking


async def complete_booking(session: AsyncSession, booking_id: int) -> Booking:
    booking = await get_booking(session, booking_id)
    if booking.status != BookingStatus.ACTIVE:
        raise BookingPermissionError(f"Cannot complete booking in status {booking.status.value}")
    booking.status = BookingStatus.COMPLETED
    await session.flush()
    return booking


async def set_booking_overrun(session: AsyncSession, booking_id: int) -> Booking:
    booking = await get_booking(session, booking_id)
    if booking.status != BookingStatus.ACTIVE:
        raise BookingPermissionError(f"Cannot set overrun on booking in status {booking.status.value}")
    booking.status = BookingStatus.OVERRUN
    await session.flush()
    return booking


async def resolve_overrun(session: AsyncSession, booking_id: int) -> Booking:
    """Admin resolved the overrun conflict — allow continuation."""
    booking = await get_booking(session, booking_id)
    if booking.status != BookingStatus.OVERRUN:
        raise BookingPermissionError(f"Booking not in overrun status: {booking.status.value}")
    booking.status = BookingStatus.ACTIVE  # back to active, allowed to continue
    await session.flush()
    return booking


__all__ = [
    "BookingConflictError",
    "BookingNotFoundError",
    "BookingPermissionError",
    "activate_booking",
    "cancel_booking",
    "complete_booking",
    "confirm_booking",
    "create_booking",
    "expire_pending_bookings",
    "get_booking",
    "list_free_slots",
    "list_my_bookings",
    "resolve_overrun",
    "set_booking_overrun",
]