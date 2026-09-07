"""APScheduler integration for deferred booking operations.

Issue #26: APScheduler jobs persisted in `scheduled_jobs` table.
Issue #27: Recovery: rebuild pending jobs from DB on startup.

Jobs are stored both in APScheduler and mirrored to `scheduled_jobs`
so they survive restarts. On startup, `recover_jobs()` rebuilds the
scheduler from DB rows.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable

from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import select

from core.logging import get_logger
from models import Booking, BookingStatus, ScheduledJob
from services import bookings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = get_logger(__name__)


# --- Job callables (registered handlers) ---

JOB_HANDLERS: dict[str, Callable[..., Any]] = {
    "auto_confirm_booking": None,  # will be set lazily
    "expire_pending_booking": None,
    "activate_booking": None,
    "complete_booking": None,
    "cancel_booking_if_unconfirmed": None,
}


def _set_handler(name: str, fn: Callable) -> None:
    """Register a callback by dotted name, e.g. 'auto_confirm_booking'."""
    JOB_HANDLERS[name] = fn


async def _job_wrapper(callback_name: str, session_factory: "async_sessionmaker", job_kwargs: dict) -> None:
    """Safely execute a job with DB session and error logging."""
    handler = JOB_HANDLERS.get(callback_name)
    if not handler:
        log.error("job_handler_missing", callback=callback_name)
        return
    async with session_factory() as session:
        try:
            await handler(session=session, **job_kwargs)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            log.exception("job_failed", callback=callback_name, error=str(exc))
            raise


# --- Default handlers ---

async def _activate_booking(session: "AsyncSession", booking_id: int) -> None:
    """Called when booking_start is reached — transition to ACTIVE."""
    try:
        b = await bookings.activate_booking(session, booking_id)
        log.info("booking_activated", booking_id=b.id)
    except bookings.BookingPermissionError as e:
        log.warning("booking_activate_skipped", booking_id=booking_id, reason=str(e))


async def _expire_pending_booking(session: "AsyncSession", booking_id: int) -> None:
    """Called 30 min before start — expire if not confirmed."""
    b = await session.get(Booking, booking_id)
    if not b:
        return
    if b.status == BookingStatus.PENDING:
        b.status = BookingStatus.EXPIRED
        b.cancellation_reason = "Auto-expired: no confirmation within deadline"
        await session.flush()
        log.info("booking_auto_expired", booking_id=booking_id)


async def _complete_booking(session: "AsyncSession", booking_id: int) -> None:
    """Called at booking_end if still active."""
    try:
        b = await bookings.complete_booking(session, booking_id)
        log.info("booking_completed", booking_id=b.id)
    except bookings.BookingPermissionError as e:
        log.warning("booking_complete_skipped", booking_id=booking_id, reason=str(e))


def init_job_handlers() -> None:
    """Register default handlers."""
    _set_handler("auto_confirm_booking", _activate_booking)
    _set_handler("expire_pending_booking", _expire_pending_booking)
    _set_handler("activate_booking", _activate_booking)
    _set_handler("complete_booking", _complete_booking)
    _set_handler("cancel_booking_if_unconfirmed", _expire_pending_booking)


# --- Scheduler lifecycle ---

class SchedulerManager:
    """Wraps AsyncIOScheduler with DB-backed persistence.

    Usage:
        sm = SchedulerManager(session_factory)
        await sm.start(app=fastapi_app)
        job_id = await sm.schedule_job(callback, run_at, kwargs)
        await sm.shutdown()
    """

    def __init__(self, session_factory: "async_sessionmaker") -> None:
        self._session_factory = session_factory
        self._scheduler: AsyncIOScheduler | None = None

    async def start(self) -> None:
        init_job_handlers()
        self._scheduler = AsyncIOScheduler(timezone=timezone.utc)
        self._scheduler.add_listener(self._on_job_executed, EVENT_JOB_EXECUTED)
        self._scheduler.add_listener(self._on_job_error, EVENT_JOB_ERROR)
        self._scheduler.start()
        log.info("scheduler_started")

        # Recover pending jobs from DB
        await self.recover_jobs()

    async def shutdown(self) -> None:
        if self._scheduler:
            await self._scheduler.shutdown(wait=False)
            log.info("scheduler_stopped")

    def _on_job_executed(self, event) -> None:
        log.info("job_executed", job_id=event.job_id)

    def _on_job_error(self, event) -> None:
        log.error("job_error", job_id=event.job_id, error=str(event.exception))

    async def recover_jobs(self) -> int:
        """Rebuild scheduler from `scheduled_jobs` table.

        Issue #27: called once at startup.
        Returns number of recovered jobs.
        """
        if not self._scheduler:
            log.warning("recover_jobs_skipped: scheduler not started")
            return 0

        async with self._session_factory() as session:
            now = datetime.now(timezone.utc)
            q = select(ScheduledJob).where(
                ScheduledJob.status == "pending",
                ScheduledJob.next_run_time > now,
            )
            jobs = list((await session.execute(q)).scalars().all())

        count = 0
        for job in jobs:
            try:
                trigger = DateTrigger(run_date=job.next_run_time)
                kwargs = json.loads(job.kwargs) if job.kwargs else {}
                args = json.loads(job.args) if job.args else []
                self._scheduler.add_job(
                    _job_wrapper,
                    trigger=trigger,
                    id=job.apscheduler_id,
                    args=[job.callback, self._session_factory, *args],
                    kwargs=kwargs,
                    misfire_grace_time=job.misfire_grace_time,
                    max_instances=job.max_instances,
                    replace_existing=True,
                )
                count += 1
            except Exception as exc:
                log.error("recover_job_failed", job_id=job.apscheduler_id, error=str(exc))

        log.info("jobs_recovered", recovered=count)
        return count

    async def schedule_at(
        self,
        callback_name: str,
        run_at: datetime,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        misfire_grace_time: int = 300,
        max_instances: int = 1,
    ) -> str:
        """Schedule a job at a specific datetime.

        Writes to DB for recovery, then adds to scheduler.
        Returns the job ID.
        """
        if not self._scheduler:
            raise RuntimeError("Scheduler not started")

        job_id = f"{callback_name}_{int(run_at.timestamp() * 1000)}_{run_at.microsecond % 100000}"
        trigger = DateTrigger(run_date=run_at)
        self._scheduler.add_job(
            _job_wrapper,
            trigger=trigger,
            id=job_id,
            args=[callback_name, self._session_factory, *(args or [])],
            kwargs=kwargs or {},
            misfire_grace_time=misfire_grace_time,
            max_instances=max_instances,
            replace_existing=True,
        )

        # Persist to DB
        async with self._session_factory() as session:
            job = ScheduledJob(
                apscheduler_id=job_id,
                callback=callback_name,
                trigger=json.dumps({"type": "date", "run_date": run_at.isoformat()}),
                args=json.dumps(args or []),
                kwargs=json.dumps(kwargs or {}),
                next_run_time=run_at,
                status="pending",
                misfire_grace_time=misfire_grace_time,
                max_instances=max_instances,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(job)
            await session.commit()
        log.info("job_scheduled", callback=callback_name, run_at=run_at.isoformat(), job_id=job_id)
        return job_id

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a pending job (DB + scheduler)."""
        if self._scheduler:
            try:
                self._scheduler.remove_job(job_id)
            except JobLookupError:
                pass
        async with self._session_factory() as session:
            job = await session.get(ScheduledJob, )
            # Lookup by apscheduler_id
            from sqlalchemy import select
            q = select(ScheduledJob).where(ScheduledJob.apscheduler_id == job_id)
            result = (await session.execute(q)).scalar_one_or_none()
            if result:
                result.status = "cancelled"
                await session.commit()
                log.info("job_cancelled", job_id=job_id)
                return True
        return False

    async def schedule_booking_jobs(self, booking: Booking) -> None:
        """Issue #28: Schedule confirmation-expiry + activation + completion jobs."""
        now = datetime.now(timezone.utc)
        start = booking.booking_start
        end = booking.booking_end

        # 1. Confirmation deadline: 30 min before start
        confirm_deadline = start - timedelta(minutes=30)
        if confirm_deadline > now:
            await self.schedule_at(
                "expire_pending_booking",
                confirm_deadline,
                args=[booking.id],
            )

        # 2. Auto-activate at start time (if still confirmed)
        if start > now:
            await self.schedule_at(
                "activate_booking",
                start,
                args=[booking.id],
            )

        # 3. Auto-complete at end time
        if end > now:
            await self.schedule_at(
                "complete_booking",
                end,
                args=[booking.id],
            )


__all__ = [
    "SchedulerManager",
    "init_job_handlers",
    "JobLookupError",
]
