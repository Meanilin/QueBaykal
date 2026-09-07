"""TV Agent - Windows service for VLC control.

Runs on the TV-connected machine, polls backend for commands.
Implements VLC CLI control with forced subtitles, audio track selection, process monitoring.
"""

from __future__ import annotations

import asyncio
import json
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
    # Playback preferences
    audio_languages: list[str] = field(default_factory=lambda: ["ru", "en"])
    subtitle_languages: list[str] = field(default_factory=lambda: ["ru", "en"])
    prefer_forced_subtitles: bool = True

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
    """Control VLC via CLI with forced subtitles and audio track selection."""

    def __init__(
        self,
        vlc_path: str = "vlc",
        vlc_args: list[str] = None,
        audio_languages: list[str] = None,
        subtitle_languages: list[str] = None,
        prefer_forced_subtitles: bool = True,
    ):
        self.vlc_path = vlc_path
        self.vlc_args = vlc_args or ["--intf", "dummy", "--no-video-title-show", "--fullscreen"]
        self.audio_languages = audio_languages or ["ru", "en"]
        self.subtitle_languages = subtitle_languages or ["ru", "en"]
        self.prefer_forced_subtitles = prefer_forced_subtitles
        
        self.process: Optional[subprocess.Popen] = None
        self.current_media: Optional[str] = None
        self.current_command_id: Optional[int] = None
        self._playback_started = asyncio.Event()
        self._monitor_task: Optional[asyncio.Task] = None

    def _build_vlc_args(self, media_path: str) -> list[str]:
        """Build VLC command line with audio/subtitle preferences."""
        args = [
            self.vlc_path,
            *self.vlc_args,
            "--audio-language", ",".join(self.audio_languages),
            "--sub-language", ",".join(self.subtitle_languages),
        ]
        
        if self.prefer_forced_subtitles:
            args.extend(["--sub-track", "0"])  # 0 = auto (forced preferred)
        else:
            args.extend(["--sub-track", "-1"])  # -1 = disabled
        
        args.append(media_path)
        return args

    async def play(self, media_path: str, command_id: int = None) -> bool:
        """Play media file via VLC CLI."""
        # Stop any existing playback
        await self.stop()
        
        self.current_command_id = command_id
        self.current_media = media_path
        self._playback_started.clear()
        
        args = self._build_vlc_args(media_path)
        log.info("vlc_play", args=" ".join(args), command_id=command_id)
        
        try:
            self.process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
            
            # Start process monitor
            self._monitor_task = asyncio.create_task(self._monitor_process())
            
            # Wait for playback to start (VLC outputs to stderr when ready)
            try:
                await asyncio.wait_for(self._playback_started.wait(), timeout=10.0)
                return True
            except asyncio.TimeoutError:
                log.warning("vlc_playback_start_timeout", media=media_path)
                return False
                
        except Exception as e:
            log.error("vlc_play_failed", error=str(e), media=media_path)
            return False

    async def stop(self) -> bool:
        """Stop VLC process."""
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        
        if self.process:
            try:
                if os.name == "nt":
                    self.process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    self.process.terminate()
                
                await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, self.process.wait),
                    timeout=5,
                )
            except asyncio.TimeoutError:
                self.process.kill()
                await asyncio.get_event_loop().run_in_executor(None, self.process.wait)
            except Exception as e:
                log.error("vlc_stop_error", error=str(e))
            
            self.process = None
            self.current_media = None
            self.current_command_id = None
        
        return True

    async def pause(self) -> bool:
        """Pause/unpause playback (via VLC hotkey simulation or DBus)."""
        # For CLI mode, we'd need DBus or remote interface
        # Simplified: not implemented for CLI-only mode
        log.warning("pause_not_implemented_cli_mode")
        return False

    async def seek(self, position: float) -> bool:
        """Seek to position (0.0 - 1.0)."""
        # Not easily done with CLI-only mode
        log.warning("seek_not_implemented_cli_mode")
        return False

    async def set_volume(self, volume: int) -> bool:
        """Set volume (0-100)."""
        log.warning("volume_not_implemented_cli_mode")
        return False

    async def _monitor_process(self):
        """Monitor VLC process stderr for playback start confirmation."""
        if not self.process or not self.process.stderr:
            return
        
        try:
            while self.running and self.process.poll() is None:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, self.process.stderr.readline
                )
                if not line:
                    break
                
                line = line.decode("utf-8", errors="ignore").strip()
                if line:
                    log.debug("vlc_stderr", line=line)
                    
                    # Check for playback start indicators
                    if any(keyword in line.lower() for keyword in [
                        "playing", "started", "buffering", "streaming",
                        "main decoder", "video output", "audio output"
                    ]):
                        if not self._playback_started.is_set():
                            self._playback_started.set()
                            # Report PLAYBACK_STARTED to backend
                            await self._report_playback_started()
            
            # Process ended
            if self.process.poll() is not None:
                log.info("vlc_process_ended", returncode=self.process.poll())
                await self._report_playback_ended()
                
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error("vlc_monitor_error", error=str(e))

    async def _report_playback_started(self):
        """Report PLAYBACK_STARTED to backend."""
        if not self.current_command_id:
            return
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{self.backend_url}/api/agent/report",
                    json={
                        "command_id": self.current_command_id,
                        "success": True,
                        "output": "PLAYBACK_STARTED",
                    },
                    headers={"Authorization": f"Bearer {self.agent_token}"},
                    timeout=5.0,
                )
        except Exception as e:
            log.error("report_playback_started_failed", error=str(e))

    async def _report_playback_ended(self):
        """Report playback ended to backend."""
        if not self.current_command_id:
            return
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{self.backend_url}/api/agent/report",
                    json={
                        "command_id": self.current_command_id,
                        "success": True,
                        "output": "PLAYBACK_ENDED",
                    },
                    headers={"Authorization": f"Bearer {self.agent_token}"},
                    timeout=5.0,
                )
        except Exception as e:
            log.error("report_playback_ended_failed", error=str(e))

    async def get_status(self) -> dict:
        """Get VLC status."""
        is_running = self.process is not None and self.process.poll() is None
        return {
            "playing": is_running,
            "media": self.current_media,
            "command_id": self.current_command_id,
        }

    # Need to set these from TVAgent
    backend_url: str = ""
    agent_token: str = ""
    running: bool = True


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
        import subprocess
        
        for ext in ("*.mp4", "*.mkv", "*.avi", "*.mov", "*.webm"):
            for file_path in self.library_path.rglob(ext):
                try:
                    # Calculate checksum (first 1MB for speed)
                    sha256 = hashlib.sha256()
                    with open(file_path, "rb") as f:
                        chunk = f.read(1024 * 1024)
                        sha256.update(chunk)
                    checksum = sha256.hexdigest()
                    
                    # Extract media info via ffprobe
                    media_info = self._extract_media_info(file_path)
                    
                    self.index[checksum] = {
                        "path": str(file_path),
                        "name": file_path.name,
                        "size": file_path.stat().st_size,
                        "checksum": checksum,
                        **media_info,
                    }
                    count += 1
                except Exception as e:
                    log.warning("media_index_failed", file=str(file_path), error=str(e))
        
        log.info("media_library_scanned", count=count)
        return count

    def _extract_media_info(self, file_path: Path) -> dict:
        """Extract duration, audio tracks, subtitle tracks using ffprobe."""
        try:
            cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(file_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return {}
            
            data = json.loads(result.stdout)
            
            # Duration
            duration = None
            if "format" in data and "duration" in data["format"]:
                duration = float(data["format"]["duration"])
            
            # Audio tracks
            audio_tracks = []
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "audio":
                    audio_tracks.append({
                        "index": stream.get("index"),
                        "codec": stream.get("codec_name"),
                        "language": stream.get("tags", {}).get("language", "und"),
                        "title": stream.get("tags", {}).get("title", ""),
                        "channels": stream.get("channels"),
                    })
            
            # Subtitle tracks
            subtitle_tracks = []
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "subtitle":
                    subtitle_tracks.append({
                        "index": stream.get("index"),
                        "codec": stream.get("codec_name"),
                        "language": stream.get("tags", {}).get("language", "und"),
                        "title": stream.get("tags", {}).get("title", ""),
                        "forced": stream.get("disposition", {}).get("forced", 0) == 1,
                        "default": stream.get("disposition", {}).get("default", 0) == 1,
                    })
            
            return {
                "duration_seconds": int(duration) if duration else None,
                "audio_tracks": json.dumps(audio_tracks) if audio_tracks else None,
                "subtitle_tracks": json.dumps(subtitle_tracks) if subtitle_tracks else None,
            }
        except Exception as e:
            log.warning("ffprobe_failed", extra={"file": str(file_path), "error": str(e)})
            return {}

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
        self.vlc = VLCController(
            vlc_path=config.vlc_path,
            vlc_args=config.vlc_args,
            audio_languages=config.audio_languages,
            subtitle_languages=config.subtitle_languages,
            prefer_forced_subtitles=config.prefer_forced_subtitles,
        )
        # Inject backend config into VLCController for reporting
        self.vlc.backend_url = config.backend_url
        self.vlc.agent_token = config.agent_token
        
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
            if command_type == "start_playback":
                media_path = payload.get("media_path")
                playback_command_id = payload.get("playback_command_id")
                if media_path:
                    success = await self.vlc.play(media_path, playback_command_id)
                    result["success"] = success
                    result["output"] = f"Playing {media_path}" if success else "Play failed"
            
            elif command_type == "play":
                media_path = payload.get("media_path") or payload.get("url")
                if media_path:
                    success = await self.vlc.play(media_path, command_id)
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
        self.vlc.running = True
        
        # Initial register
        await self.register()
        
        # Initial media index
        self.library.scan()
        await self.report_media_index()
        
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
        self.vlc.running = False
        
        if self._poll_task:
            self._poll_task.cancel()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        
        await self.vlc.stop()
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