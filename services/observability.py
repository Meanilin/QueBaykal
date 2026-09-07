"""Observability service — audit log, metrics, correlation IDs.

Issues #81-#86: audit_log model, admin event logging, log correlation,
Prometheus metrics, Sentry integration, /status command.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AuditAction, AuditLog, User

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


class ObservabilityError(Exception):
    pass


# --- Audit log ---

async def log_admin_action(
    session: AsyncSession,
    *,
    action: AuditAction,
    chat_id: int | None = None,
    user_id: int | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    """Log an admin action to audit log."""
    entry = AuditLog(
        chat_id=chat_id,
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details_json=json.dumps(details) if details else None,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    session.add(entry)
    await session.flush()
    return entry


async def get_audit_log(
    session: AsyncSession,
    *,
    chat_id: int | None = None,
    user_id: int | None = None,
    action: AuditAction | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditLog]:
    """Query audit log entries."""
    q = select(AuditLog).order_by(AuditLog.created_at.desc())
    
    if chat_id:
        q = q.where(AuditLog.chat_id == chat_id)
    if user_id:
        q = q.where(AuditLog.user_id == user_id)
    if action:
        q = q.where(AuditLog.action == action)
    
    q = q.limit(limit).offset(offset)
    return list((await session.execute(q)).scalars().all())


# --- Correlation ID helpers ---

def extract_correlation_id(
    request_id: str | None = None,
    session_id: int | None = None,
    booking_id: int | None = None,
    vote_session_id: int | None = None,
) -> str:
    """Build correlation context string for logging."""
    parts = []
    if request_id:
        parts.append(f"req={request_id}")
    if session_id:
        parts.append(f"vote={session_id}")
    if booking_id:
        parts.append(f"booking={booking_id}")
    if vote_session_id:
        parts.append(f"vs={vote_session_id}")
    return " ".join(parts) if parts else "-"


# --- Prometheus metrics ---

class MetricsCollector:
    """Collect and expose Prometheus metrics."""
    
    def __init__(self):
        self._votes_total = 0
        self._votes_by_type = {"fire": 0, "final": 0}
        self._downloads_total = 0
        self._downloads_success = 0
        self._downloads_failed = 0
        self._agent_online = 0
        self._agent_offline = 0
        self._bookings_created = 0
        self._bookings_completed = 0
        self._bookings_cancelled = 0
        self._bookings_overrun = 0
    
    def inc_votes(self, vote_type: str):
        self._votes_total += 1
        if vote_type in self._votes_by_type:
            self._votes_by_type[vote_type] += 1
    
    def inc_download(self, success: bool):
        self._downloads_total += 1
        if success:
            self._downloads_success += 1
        else:
            self._downloads_failed += 1
    
    def inc_agent_status(self, online: bool):
        if online:
            self._agent_online += 1
        else:
            self._agent_offline += 1
    
    def inc_booking(self, status: str):
        if status == "created":
            self._bookings_created += 1
        elif status == "completed":
            self._bookings_completed += 1
        elif status == "cancelled":
            self._bookings_cancelled += 1
        elif status == "overrun":
            self._bookings_overrun += 1
    
    def get_metrics(self) -> str:
        """Generate Prometheus-format metrics."""
        lines = [
            "# HELP cinema_votes_total Total votes cast",
            "# TYPE cinema_votes_total counter",
            f"cinema_votes_total {self._votes_total}",
            f"cinema_votes_fire_total {self._votes_by_type['fire']}",
            f"cinema_votes_final_total {self._votes_by_type['final']}",
            "",
            "# HELP cinema_downloads_total Total download attempts",
            "# TYPE cinema_downloads_total counter",
            f"cinema_downloads_total {self._downloads_total}",
            f"cinema_downloads_success_total {self._downloads_success}",
            f"cinema_downloads_failed_total {self._downloads_failed}",
            "",
            "# HELP cinema_agent_status Agent online/offline count",
            "# TYPE cinema_agent_status gauge",
            f"cinema_agent_online {self._agent_online}",
            f"cinema_agent_offline {self._agent_offline}",
            "",
            "# HELP cinema_bookings_total Total bookings by status",
            "# TYPE cinema_bookings_total counter",
            f"cinema_bookings_created_total {self._bookings_created}",
            f"cinema_bookings_completed_total {self._bookings_completed}",
            f"cinema_bookings_cancelled_total {self._bookings_cancelled}",
            f"cinema_bookings_overrun_total {self._bookings_overrun}",
        ]
        return "\n".join(lines)


# Global metrics instance
metrics = MetricsCollector()


# --- Sentry integration (optional) ---

def init_sentry(dsn: str | None = None):
    """Initialize Sentry SDK if DSN provided."""
    if not dsn:
        log.info("sentry_disabled")
        return
    
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
        
        sentry_logging = LoggingIntegration(
            level=logging.INFO,
            event_level=logging.ERROR,
        )
        
        sentry_sdk.init(
            dsn=dsn,
            integrations=[sentry_logging],
            traces_sample_rate=0.1,
        )
        log.info("sentry_initialized")
    except ImportError:
        log.warning("sentry_not_installed")
    except Exception as e:
        log.error("sentry_init_failed", error=str(e))


# --- Health/status check ---

async def get_system_status(session: AsyncSession) -> dict:
    """Get comprehensive system status for /status command."""
    from sqlalchemy import func, select
    from models import Booking, Chat, TVDevice, VoteSession
    
    # Count chats
    chat_count = (await session.execute(
        select(func.count(Chat.id))
    )).scalar()
    
    # Count active bookings
    active_bookings = (await session.execute(
        select(func.count(Booking.id)).where(
            Booking.status.in_(["pending", "confirmed", "active"])
        )
    )).scalar()
    
    # Count active vote sessions
    active_votes = (await session.execute(
        select(func.count(VoteSession.id)).where(
            VoteSession.state.in_([
                "suggestions", "filter", "final_vote", "downloading", "ready", "playing"
            ])
        )
    )).scalar()
    
    # TV devices
    tv_online = (await session.execute(
        select(func.count(TVDevice.id)).where(TVDevice.status == "online")
    )).scalar()
    tv_total = (await session.execute(
        select(func.count(TVDevice.id))
    )).scalar()
    
    # Recent audit log entries
    recent_audit = (await session.execute(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(10)
    )).scalars().all()
    
    return {
        "chats": chat_count,
        "active_bookings": active_bookings,
        "active_vote_sessions": active_votes,
        "tv_devices": {"online": tv_online, "total": tv_total},
        "recent_audit": [
            {
                "action": a.action.value,
                "target": f"{a.target_type}:{a.target_id}" if a.target_type else None,
                "user_id": a.user_id,
                "chat_id": a.chat_id,
                "time": a.created_at.isoformat(),
            }
            for a in recent_audit
        ],
        "metrics": metrics.get_metrics(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


__all__ = [
    "ObservabilityError",
    "log_admin_action",
    "get_audit_log",
    "extract_correlation_id",
    "MetricsCollector",
    "metrics",
    "init_sentry",
    "get_system_status",
]