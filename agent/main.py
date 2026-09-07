"""TV Agent - Windows service for VLC control.

Runs on the TV-connected machine, polls backend for commands.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import httpx
import yaml

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    device_id: str
    agent_token: str
    tv_hostname: str
    media_library_path: str
    backend_url: str
    poll_interval: int = 5
    heartbeat_interval: int = 10
    vlc_path: str = "vlc"
    vlc_args: list[str] = field(default_factory=lambda: ["--intf", "dummy", "--no-video-title-show"])

    @classmethod
    def from_file(cls, path: str) -> "AgentConfig":
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**data)

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(
            device_id=os.environ["DEVICE_ID"],
            agent_token=os.environ["AGENT_TOKEN"],
            tv_hostname=os.environ.get("TV_HOSTNAME", "localhost"),
            media_library_path=os.environ["MEDIA_LIBRARY_PATH"],
            backend_url=os.environ["BACKEND_URL"],
            poll_interval=int(os.environ.get("POLL_INTERVAL", "5")),
            heartbeat_interval=int(os.environ.get("HEARTBEAT_INTERVAL", "10")),
            vlc_path=os.environ.get("VLC_PATH", "vlc"),
        )


class VLCController:
    """Control VLC via HTTP interface or CLI."""

    def __init__(self, vlc_path: str = "vlc", vlc_args: list[str] = None):
        self.vlc_path = vlc_path
        self.vlc_args = vlc_args or ["--intf", "dummy", "--no-video-title-show"]
        self.process: Optional[subprocess.Popen] = None
        self.current_media: Optional[str] = None
        self._vlc_http_port = 8081
        self._vlc_http_password = "cinema-bot"

    async def start_vlc(self) -> bool:
        """Start VLC with HTTP interface."""
        if self.process and self.process.poll() is None:
            return True

        args = [
            self.vlc_path,
            *self.vlc_args,
            "--extraintf", "http",
            "--http-host", "0.0.0.0",
            "--http-port", str(self._vlc_http_port),
            "--http-password", self._vlc_http_password,
        ]
        try:
            self.process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Wait for VLC to start
            await asyncio.sleep(2)
            return self.process.poll() is None
        except Exception as e:
            log.error("vlc_start_failed", error=str(e))
            return False

    async def stop_vlc(self):
        """Stop VLC process."""
        if self.process:
            self.process.terminate()
            try:
                await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, self.process.wait),
                    timeout=5,
                )
            except asyncio.TimeoutError:
                self.process.kill()
                await asyncio.get_event_loop().run_in_executor(None, self.process.wait)
            self.process = None
            self.current_media = None

    async def play(self, media_path: str) -> bool:
        """Play media file."""
        await self.start_vlc()
        
        # Use VLC HTTP interface
        url = f"http://localhost:{self._vlc_http_port}/requests/status.xml"
        auth = ("", self._vlc_http_password)
        
        async with httpx.AsyncClient() as client:
            try:
                # Clear playlist
                await client.post(
                    f"http://localhost:{self._vlc_http_port}/requests/status.xml",
                    params={"command": "pl_empty"},
                    auth=auth,
                )
                # Add media
                await client.post(
                    f"http://localhost:{self._vlc_http_port}/requests/status.xml",
                    params={"command": "in_play", "input": media_path},
                    auth=auth,
                )
                self.current_media = media_path
                return True
            except Exception as e:
                log.error("vlc_play_failed", error=str(e), media=media_path)
                return False

    async def stop(self) -> bool:
        """Stop playback."""
        if not self.process:
            return True
        
        url = f"http://localhost:{self._vlc_http_port}/requests/status.xml"
        auth = ("", self._vlc_http_password)
        
        async with httpx.AsyncClient() as client:
            try:
                await client.post(
                    url,
                    params={"command": "pl_stop"},
                    auth=auth,
                )
                self.current_media = None
                return True
            except Exception as e:
                log.error("vlc_stop_failed", error=str(e))
                return False

    async def pause(self) -> bool:
        """Pause playback."""
        url = f"http://localhost:{self._vlc_http_port}/requests/status.xml"
        auth = ("", self._vlc_http_password)
        
        async with httpx.AsyncClient() as client:
            try:
                await client.post(
                    url,
                    params={"command": "pl_pause"},
                    auth=auth,
                )
                return True
            except Exception as e:
                log.error("vlc_pause_failed", error=str(e))
                return False

    async def seek(self, position: float) -> bool:
        """Seek to position (0.0 - 1.0)."""
        url = f"http://localhost:{self._vlc_http_port}/requests/status.xml"
        auth = ("", self._vlc_http_password)
        
        async with httpx.AsyncClient() as client:
            try:
                await client.post(
                    url,
                    params={"command": "seek", "val": str(position)},
                    auth=auth,
                )
                return True
            except Exception as e:
                log.error("vlc_seek_failed", error=str(e))
                return False

    async def set_volume(self, volume: int) -> bool:
        """Set volume (0-100)."""
        url = f"http://localhost:{self._vlc_http_port}/requests/status.xml"
        auth = ("", self._vlc_http_password)
        
        async with httpx.AsyncClient() as client:
            try:
                await client.post(
                    url,
                    params={"command": "volume", "val": str(volume)},
                    auth=auth,
                )
                return True
            except Exception as e:
                log.error("vlc_volume_failed", error=str(e))
                return False

    async def get_status(self) -> dict:
        """Get VLC status."""
        url = f"http://localhost:{self._vlc_http_port}/requests/status.xml"
        auth = ("", self._vlc_http_password)
        
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(url, auth=auth, timeout=2.0)
                # Parse XML status
                # Simplified - real implementation would parse XML
                return {"playing": self.current_media is not None}
            except Exception:
                return {"playing": False}


class MediaLibrary:
    """Index and search local media library."""

    def __init__(self, library_path: str):
        self.library_path = Path(library_path)
        self.index: dict[str, dict] = {}  # checksum -> metadata

    def scan(self) -> int:
        """Scan library and build index."""
        count = 0
        import hashlib
        import json
        
        for ext in ("*.mp4", "*.mkv", "*.avi", "*.mov", "*.webm"):
            for file_path in self.library_path.rglob(ext):
                try:
                    # Calculate checksum (first 1MB for speed)
                    sha256 = hashlib.sha256()
                    with open(file_path, "rb") as f:
                        chunk = f.read(1024 * 1024)
                        sha256.update(chunk)
                    checksum = sha256.hexdigest()
                    
                    self.index[checksum] = {
                        "path": str(file_path),
                        "name": file_path.name,
                        "size": file_path.stat().st_size,
                        "checksum": checksum,
                    }
                    count += 1
                except Exception as e:
                    log.warning("media_index_failed", file=str(file_path), error=str(e))
        
        log.info("media_library_scanned", count=count)
        return count

    def find_by_checksum(self, checksum: str) -> Optional[dict]:
        """Find file by checksum."""
        return self.index.get(checksum)

    def get_all(self) -> list[dict]:
        """Get all indexed files."""
        return list(self.index.values())


class TVAgent:
    """Main TV Agent class."""

    def __init__(self, config: AgentConfig):
        self.config = config
        self.vlc = VLCController(config.vlc_path, config.vlc_args)
        self.library = MediaLibrary(config.media_library_path)
        self.client = httpx.AsyncClient(
            base_url=config.backend_url,
            timeout=30.0,
            headers={"Authorization": f"Bearer {config.agent_token}"},
        )
        self.running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._last_command_id = 0

    async def register(self) -> bool:
        """Register with backend."""
        try:
            resp = await self.client.post(
                "/api/agent/register",
                json={
                    "device_id": self.config.device_id,
                    "hostname": self.config.tv_hostname,
                    "media_library_path": self.config.media_library_path,
                },
            )
            resp.raise_for_status()
            log.info("agent_registered", device_id=self.config.device_id)
            return True
        except Exception as e:
            log.error("agent_register_failed", error=str(e))
            return False

    async def poll_commands(self):
        """Poll backend for commands."""
        try:
            resp = await self.client.get(
                "/api/agent/poll",
                params={"device_id": self.config.device_id, "since_id": self._last_command_id},
            )
            resp.raise_for_status()
            data = resp.json()
            
            for cmd in data.get("commands", []):
                self._last_command_id = max(self._last_command_id, cmd["id"])
                await self.execute_command(cmd)
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                log.error("agent_poll_failed", status=e.response.status_code)
        except Exception as e:
            log.error("agent_poll_error", error=str(e))

    async def execute_command(self, cmd: dict):
        """Execute a command from backend."""
        command_type = cmd["type"]
        payload = cmd.get("payload", {})
        command_id = cmd["id"]
        
        log.info("agent_executing", command_id=command_id, type=command_type)
        
        result = {"command_id": command_id, "success": False, "output": ""}
        
        try:
            if command_type == "play":
                media_path = payload.get("media_path") or payload.get("url")
                if media_path:
                    success = await self.vlc.play(media_path)
                    result["success"] = success
                    result["output"] = f"Playing {media_path}" if success else "Play failed"
            
            elif command_type == "stop":
                success = await self.vlc.stop()
                result["success"] = success
                result["output"] = "Stopped" if success else "Stop failed"
            
            elif command_type == "pause":
                success = await self.vlc.pause()
                result["success"] = success
                result["output"] = "Paused" if success else "Pause failed"
            
            elif command_type == "seek":
                position = payload.get("position", 0)
                success = await self.vlc.seek(position)
                result["success"] = success
                result["output"] = f"Seeked to {position}" if success else "Seek failed"
            
            elif command_type == "volume":
                volume = payload.get("volume", 50)
                success = await self.vlc.set_volume(volume)
                result["success"] = success
                result["output"] = f"Volume set to {volume}" if success else "Volume failed"
            
            elif command_type == "index_media":
                count = self.library.scan()
                await self.report_media_index()
                result["success"] = True
                result["output"] = f"Indexed {count} files"
            
            elif command_type == "get_status":
                status = await self.vlc.get_status()
                result["success"] = True
                result["output"] = str(status)
            
            else:
                result["output"] = f"Unknown command: {command_type}"
        
        except Exception as e:
            result["output"] = f"Error: {e}"
            log.error("agent_command_error", command_id=command_id, error=str(e))
        
        # Report result
        await self.report_result(result)

    async def report_result(self, result: dict):
        """Report command execution result."""
        try:
            await self.client.post("/api/agent/report", json=result)
        except Exception as e:
            log.error("agent_report_failed", error=str(e))

    async def send_heartbeat(self):
        """Send heartbeat to backend."""
        try:
            vlc_status = await self.vlc.get_status()
            await self.client.post(
                "/api/agent/heartbeat",
                json={
                    "device_id": self.config.device_id,
                    "status": "online",
                    "vlc_status": vlc_status,
                    "current_media": self.vlc.current_media,
                },
            )
        except Exception as e:
            log.error("agent_heartbeat_failed", error=str(e))

    async def report_media_index(self):
        """Report media library index to backend."""
        try:
            files = self.library.get_all()
            await self.client.post(
                "/api/agent/media_index",
                json={
                    "device_id": self.config.device_id,
                    "files": files,
                },
            )
        except Exception as e:
            log.error("agent_media_index_failed", error=str(e))

    async def run(self):
        """Main agent loop."""
        self.running = True
        
        # Initial register
        await self.register()
        
        # Initial media index
        self.library.scan()
        await self.report_media_index()
        
        # Start VLC
        await self.vlc.start_vlc()
        
        # Start background tasks
        self._poll_task = asyncio.create_task(self._poll_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        
        try:
            await asyncio.gather(self._poll_task, self._heartbeat_task)
        except asyncio.CancelledError:
            pass

    async def _poll_loop(self):
        while self.running:
            await self.poll_commands()
            await asyncio.sleep(self.config.poll_interval)

    async def _heartbeat_loop(self):
        while self.running:
            await self.send_heartbeat()
            await asyncio.sleep(self.config.heartbeat_interval)

    async def shutdown(self):
        """Graceful shutdown."""
        self.running = False
        
        if self._poll_task:
            self._poll_task.cancel()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        
        await self.vlc.stop_vlc()
        await self.client.aclose()
        log.info("agent_shutdown_complete")


async def main():
    """Entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    
    # Load config
    config_path = os.environ.get("CONFIG_FILE", "/etc/cinema-bot/agent.yaml")
    if Path(config_path).exists():
        config = AgentConfig.from_file(config_path)
    else:
        config = AgentConfig.from_env()
    
    agent = TVAgent(config)
    
    # Handle signals
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(agent.shutdown()))
    
    try:
        await agent.run()
    except Exception as e:
        log.error("agent_fatal_error", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())