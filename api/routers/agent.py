"""Agent API routes for TV Agent communication.

Issues #59-#66: /api/agent/poll, /report, /heartbeat, /register, /media_index
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_session
from models import TVDevice, TVDeviceStatus

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])


# --- Schemas ---

class RegisterRequest(BaseModel):
    device_id: str
    hostname: str
    media_library_path: str


class RegisterResponse(BaseModel):
    device_id: str
    agent_token: str
    status: str


class PollResponse(BaseModel):
    commands: list[dict] = Field(default_factory=list)


class CommandPayload(BaseModel):
    id: int
    type: str
    payload: dict = Field(default_factory=dict)
    created_at: datetime


class ReportRequest(BaseModel):
    command_id: int
    success: bool
    output: str = ""
    event: str | None = None  # PLAYBACK_STARTED, PLAYBACK_ENDED, ERROR


class HeartbeatRequest(BaseModel):
    device_id: str
    status: str
    vlc_status: dict = Field(default_factory=dict)
    current_media: str | None = None


class MediaIndexRequest(BaseModel):
    device_id: str
    files: list[dict] = Field(default_factory=list)


# --- In-memory command queue (replace with Redis/DB in production) ---
_command_queues: dict[str, list[dict]] = {}
_command_counter = 0


def _get_queue(device_id: str) -> list[dict]:
    if device_id not in _command_queues:
        _command_queues[device_id] = []
    return _command_queues[device_id]


def _add_command(device_id: str, cmd_type: str, payload: dict) -> int:
    global _command_counter
    _command_counter += 1
    cmd = {
        "id": _command_counter,
        "type": cmd_type,
        "payload": payload,
        "created_at": datetime.utcnow().isoformat(),
    }
    _get_queue(device_id).append(cmd)
    return _command_counter


# --- Dependency: verify agent token ---

async def verify_agent_token(
    request: Request,
    session: AsyncSession = Depends(get_session),
    authorization: str = Header(None),
) -> TVDevice:
    """Verify agent token and return TVDevice."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    
    token = authorization[7:]  # Remove "Bearer "
    
    q = select(TVDevice).where(TVDevice.agent_token_hash == token)
    device = (await session.execute(q)).scalar_one_or_none()
    
    if not device:
        raise HTTPException(401, "Invalid agent token")
    
    # Update last heartbeat
    device.last_heartbeat_at = datetime.utcnow()
    device.status = TVDeviceStatus.ONLINE
    await session.commit()
    
    return device


# --- Routes ---

@router.post("/register", response_model=RegisterResponse)
async def register_agent(
    request: Request,
    data: RegisterRequest,
    session: AsyncSession = Depends(get_session),
):
    """Register a new TV agent device."""
    # Check if device already exists
    q = select(TVDevice).where(TVDevice.device_id == data.device_id)
    device = (await session.execute(q)).scalar_one_or_none()
    
    if device:
        # Update existing
        device.hostname = data.hostname
        device.status = TVDeviceStatus.ONLINE
        device.last_heartbeat_at = datetime.utcnow()
    else:
        # Create new device with generated token
        import secrets
        agent_token = secrets.token_urlsafe(32)
        device = TVDevice(
            device_id=data.device_id,
            display_name=data.hostname,
            hostname=data.hostname,
            agent_token_hash=agent_token,  # In production, hash this
            status=TVDeviceStatus.ONLINE,
            last_heartbeat_at=datetime.utcnow(),
        )
        session.add(device)
    
    await session.commit()
    await session.refresh(device)
    
    log.info("agent_registered", device_id=device.device_id)
    
    return RegisterResponse(
        device_id=device.device_id,
        agent_token=device.agent_token_hash,  # Return plain token for initial setup
        status=device.status.value,
    )


@router.get("/poll", response_model=PollResponse)
async def poll_commands(
    request: Request,
    device_id: str,
    since_id: int = 0,
    device: TVDevice = Depends(verify_agent_token),
):
    """Poll for pending commands."""
    if device.device_id != device_id:
        raise HTTPException(403, "Device ID mismatch")
    
    queue = _get_queue(device_id)
    commands = [cmd for cmd in queue if cmd["id"] > since_id]
    
    return PollResponse(commands=commands)


@router.post("/report")
async def report_result(
    request: Request,
    data: ReportRequest,
    device: TVDevice = Depends(verify_agent_token),
):
    """Report command execution result."""
    # In production, store result in DB
    log.info("agent_report", device_id=device.device_id, command_id=data.command_id, success=data.success)
    
    # Remove from queue
    queue = _get_queue(device.device_id)
    _command_queues[device.device_id] = [c for c in queue if c["id"] != data.command_id]
    
    return {"status": "ok"}


@router.post("/heartbeat")
async def heartbeat(
    request: Request,
    data: HeartbeatRequest,
    device: TVDevice = Depends(verify_agent_token),
):
    """Receive heartbeat from agent."""
    if device.device_id != data.device_id:
        raise HTTPException(403, "Device ID mismatch")
    
    device.last_heartbeat_at = datetime.utcnow()
    device.status = TVDeviceStatus.ONLINE
    
    session: AsyncSession = request.app.state.session_factory()
    async with session:
        await session.merge(device)
        await session.commit()
    
    return {"status": "ok"}


@router.post("/media_index")
async def media_index(
    request: Request,
    data: MediaIndexRequest,
    device: TVDevice = Depends(verify_agent_token),
):
    """Receive media library index from agent."""
    if device.device_id != data.device_id:
        raise HTTPException(403, "Device ID mismatch")
    
    log.info("agent_media_index", device_id=device.device_id, file_count=len(data.files))
    
    # In production, store in DB for local library lookup
    # For now, just acknowledge
    return {"status": "ok", "received": len(data.files)}


# --- Internal API for backend to queue commands ---

async def queue_start_playback_command(device_id: str, playback_command_id: int, media_path: str) -> int:
    """Queue a START_PLAYBACK command for the agent (Epic 5.1)."""
    return _add_command(device_id, "start_playback", {
        "playback_command_id": playback_command_id,
        "media_path": media_path,
    })

async def queue_play_command(device_id: str, media_path: str) -> int:
    """Queue a play command for the agent."""
    return _add_command(device_id, "play", {"media_path": media_path})


async def queue_stop_command(device_id: str) -> int:
    return _add_command(device_id, "stop", {})


async def queue_pause_command(device_id: str) -> int:
    return _add_command(device_id, "pause", {})


async def queue_seek_command(device_id: str, position: float) -> int:
    return _add_command(device_id, "seek", {"position": position})


async def queue_volume_command(device_id: str, volume: int) -> int:
    return _add_command(device_id, "volume", {"volume": volume})


async def queue_index_media_command(device_id: str) -> int:
    return _add_command(device_id, "index_media", {})


async def queue_get_status_command(device_id: str) -> int:
    return _add_command(device_id, "get_status", {})


# --- Watchdog: check for stale agents ---

async def check_agent_heartbeats(session: AsyncSession, threshold_seconds: int = 30):
    """Check for agents that haven't sent heartbeat recently."""
    cutoff = datetime.utcnow() - timedelta(seconds=threshold_seconds)
    
    q = select(TVDevice).where(
        TVDevice.status == TVDeviceStatus.ONLINE,
        TVDevice.last_heartbeat_at < cutoff,
    )
    stale_devices = (await session.execute(q)).scalars().all()
    
    for device in stale_devices:
        device.status = TVDeviceStatus.OFFLINE
        log.warning("agent_stale", device_id=device.device_id, last_heartbeat=device.last_heartbeat_at)
    
    if stale_devices:
        await session.commit()
    
    return stale_devices


__all__ = [
    "router",
    "queue_play_command",
    "queue_stop_command",
    "queue_pause_command",
    "queue_seek_command",
    "queue_volume_command",
    "queue_index_media_command",
    "queue_get_status_command",
    "check_agent_heartbeats",
]