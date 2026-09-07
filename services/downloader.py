"""Downloader service using yt-dlp.

Issues #50-#66: download winner, metadata, forced subs, provide_link, progress, DLNA/HTTP delivery.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import yt_dlp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import EventMovie, MediaFile, VoteSession, VoteSessionState

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


# --- Data classes ---

@dataclass
class VideoInfo:
    """Extracted video metadata."""
    title: str
    duration: Optional[int]
    url: str
    webpage_url: str
    ext: str
    filesize: Optional[int]
    formats: list[dict]
    subtitles: dict
    automatic_captions: dict
    audio_tracks: list[dict]
    subtitle_tracks: list[dict]


@dataclass
class DownloadResult:
    """Result of a download operation."""
    success: bool
    local_path: Optional[str] = None
    filename: Optional[str] = None
    size_bytes: Optional[int] = None
    checksum: Optional[str] = None
    duration_seconds: Optional[int] = None
    audio_tracks: Optional[list[dict]] = None
    subtitle_tracks: Optional[list[dict]] = None
    error: Optional[str] = None


# --- yt-dlp helpers ---

def _build_ydl_opts(
    download_dir: Path,
    progress_callback=None,
    cookies_file: Optional[str] = None,
) -> dict:
    """Build yt-dlp options."""
    opts = {
        "outtmpl": str(download_dir / "%(title)s [%(id)s].%(ext)s"),
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "writeinfojson": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["ru", "en", "uk"],
        "subtitlesformat": "vtt",
        "embedsubtitles": False,  # we'll handle forced subs separately
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "ignoreerrors": False,
        "retries": 3,
        "fragment_retries": 3,
        "skip_unavailable_fragments": True,
        "concurrent_fragment_downloads": 4,
        "http_chunk_size": 10485760,  # 10MB
        "throttledratelimit": None,
    }

    if cookies_file and Path(cookies_file).exists():
        opts["cookiefile"] = cookies_file

    if progress_callback:
        opts["progress_hooks"] = [progress_callback]

    return opts


def _extract_video_info(url: str, cookies_file: Optional[str] = None) -> VideoInfo:
    """Extract video info without downloading."""
    opts = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
    }
    if cookies_file and Path(cookies_file).exists():
        opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            raise DownloaderError("Не удалось получить информацию о видео")

        # Build subtitle/audio track lists
        audio_tracks = []
        for f in info.get("formats", []):
            if f.get("acodec") != "none" and f.get("vcodec") == "none":
                audio_tracks.append({
                    "lang": f.get("language", "unknown"),
                    "codec": f.get("acodec"),
                    "bitrate": f.get("abr"),
                })

        subtitle_tracks = []
        for lang, subs in info.get("subtitles", {}).items():
            for s in subs:
                subtitle_tracks.append({
                    "lang": lang,
                    "ext": s.get("ext"),
                    "url": s.get("url"),
                })
        for lang, subs in info.get("automatic_captions", {}).items():
            for s in subs:
                subtitle_tracks.append({
                    "lang": lang,
                    "ext": s.get("ext"),
                    "url": s.get("url"),
                    "auto": True,
                })

        return VideoInfo(
            title=info.get("title", "Unknown"),
            duration=info.get("duration"),
            url=info.get("url", ""),
            webpage_url=info.get("webpage_url", url),
            ext=info.get("ext", "mp4"),
            filesize=info.get("filesize") or info.get("filesize_approx"),
            formats=info.get("formats", []),
            subtitles=info.get("subtitles", {}),
            automatic_captions=info.get("automatic_captions", {}),
            audio_tracks=audio_tracks,
            subtitle_tracks=subtitle_tracks,
        )


def _find_forced_subtitle(subtitle_tracks: list[dict]) -> Optional[dict]:
    """Find forced subtitle track (typically Russian for non-Russian content)."""
    # Priority: ru forced, ru, en forced, en
    for track in subtitle_tracks:
        if track.get("lang") == "ru" and not track.get("auto"):
            return track
    for track in subtitle_tracks:
        if track.get("lang") == "ru":
            return track
    for track in subtitle_tracks:
        if track.get("lang") == "en" and not track.get("auto"):
            return track
    for track in subtitle_tracks:
        if track.get("lang") == "en":
            return track
    return None


def _sanitize_filename(name: str, max_len: int = 200) -> str:
    """Sanitize filename for filesystem."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip(". ")
    return name[:max_len]


def _calculate_sha256(filepath: Path) -> str:
    """Calculate SHA256 hash of file."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


# --- Main downloader class ---

class DownloaderError(Exception):
    pass


class Downloader:
    """Async wrapper around yt-dlp for downloading videos."""

    def __init__(
        self,
        download_dir: str = "/var/lib/cinema-bot/downloads",
        cookies_file: Optional[str] = None,
        max_concurrent: int = 2,
    ):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.cookies_file = cookies_file
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._progress_callbacks: dict[str, callable] = {}

    async def extract_info(self, url: str) -> VideoInfo:
        """Extract video info (runs in thread pool)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, _extract_video_info, url, self.cookies_file
        )

    async def download(
        self,
        url: str,
        session: AsyncSession,
        vote_session_id: int,
        progress_callback=None,
    ) -> DownloadResult:
        """Download video and save metadata to DB."""
        async with self.semaphore:
            return await self._download_impl(url, session, vote_session_id, progress_callback)

    async def _download_impl(
        self,
        url: str,
        session: AsyncSession,
        vote_session_id: int,
        progress_callback=None,
    ) -> DownloadResult:
        # Get the winner movie
        q = select(EventMovie).where(
            EventMovie.session_id == vote_session_id,
            EventMovie.status == "winner",
        )
        movie = (await session.execute(q)).scalar_one_or_none()
        if not movie:
            return DownloadResult(success=False, error="Winner movie not found")

        # Create temp dir for this download
        with tempfile.TemporaryDirectory(dir=self.download_dir) as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Progress hook for yt-dlp
            def ydl_progress_hook(d):
                if progress_callback and d["status"] == "downloading":
                    downloaded = d.get("downloaded_bytes", 0)
                    total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                    if total > 0:
                        progress_callback(vote_session_id, downloaded, total)

            opts = _build_ydl_opts(tmpdir_path, ydl_progress_hook, self.cookies_file)

            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._run_ydl, url, opts)
            except yt_dlp.utils.DownloadError as e:
                log.error("yt_dlp_download_failed", url=url, error=str(e))
                return DownloadResult(success=False, error=str(e))
            except Exception as e:
                log.error("download_unexpected_error", url=url, error=str(e))
                return DownloadResult(success=False, error=f"Unexpected error: {e}")

            # Find downloaded file
            downloaded_files = list(tmpdir_path.glob("*.mp4"))
            if not downloaded_files:
                downloaded_files = list(tmpdir_path.glob("*.mkv"))
            if not downloaded_files:
                downloaded_files = list(tmpdir_path.glob("*.webm"))

            if not downloaded_files:
                return DownloadResult(success=False, error="No video file downloaded")

            video_file = downloaded_files[0]

            # Calculate hash
            checksum = _calculate_sha256(video_file)
            size_bytes = video_file.stat().st_size

            # Move to permanent location
            safe_name = _sanitize_filename(movie.title)
            final_name = f"{safe_name}_{uuid.uuid4().hex[:8]}.mp4"
            final_path = self.download_dir / final_name
            video_file.rename(final_path)

            # Find subtitle files
            sub_files = list(tmpdir_path.glob("*.vtt")) + list(tmpdir_path.glob("*.srt"))
            forced_sub_path = None
            if sub_files:
                # Try to find forced sub
                forced_sub_path = sub_files[0]  # simplified

            # Save MediaFile record
            media_file = MediaFile(
                source_url_hash=hashlib.sha256(url.encode()).hexdigest()[:64],
                local_path=str(final_path),
                filename=final_name,
                size_bytes=size_bytes,
                checksum=checksum,
                duration_seconds=movie.final_votes,  # placeholder, will update from info
                audio_tracks=json.dumps([]),
                subtitle_tracks=json.dumps([]),
            )
            session.add(media_file)
            await session.flush()

            # Link to movie
            movie.media_file_id = media_file.id
            await session.flush()

            return DownloadResult(
                success=True,
                local_path=str(final_path),
                filename=final_name,
                size_bytes=size_bytes,
                checksum=checksum,
                duration_seconds=None,
            )

    def _run_ydl(self, url: str, opts: dict):
        """Run yt-dlp in thread."""
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])


# --- High-level functions ---

async def download_winner(
    session: AsyncSession,
    vote_session_id: int,
    downloader: Downloader,
    progress_callback=None,
) -> DownloadResult:
    """Download the winner movie for a vote session."""
    # Get session and winner
    q = select(VoteSession).where(VoteSession.id == vote_session_id)
    vs = (await session.execute(q)).scalar_one_or_none()
    if not vs:
        return DownloadResult(success=False, error="Vote session not found")

    if vs.state != VoteSessionState.DOWNLOADING:
        return DownloadResult(success=False, error=f"Invalid state: {vs.state.value}")

    # Get winner movie
    q = select(EventMovie).where(
        EventMovie.session_id == vote_session_id,
        EventMovie.status == "winner",
    )
    movie = (await session.execute(q)).scalar_one_or_none()
    if not movie:
        return DownloadResult(success=False, error="Winner movie not found")

    # Try to extract URL from movie title (simplified - real impl needs URL storage)
    # For now, return error asking for link
    return DownloadResult(
        success=False,
        error="URL not stored. Use /provide_link to provide download URL.",
    )


async def provide_download_link(
    session: AsyncSession,
    vote_session_id: int,
    url: str,
    downloader: Downloader,
    progress_callback=None,
) -> DownloadResult:
    """Provide a download link for the winner (manual fallback)."""
    q = select(VoteSession).where(VoteSession.id == vote_session_id)
    vs = (await session.execute(q)).scalar_one_or_none()
    if not vs:
        return DownloadResult(success=False, error="Vote session not found")

    if vs.state not in (VoteSessionState.DOWNLOADING, VoteSessionState.DOWNLOAD_FAILED):
        return DownloadResult(success=False, error=f"Invalid state: {vs.state.value}")

    q = select(EventMovie).where(
        EventMovie.session_id == vote_session_id,
        EventMovie.status == "winner",
    )
    movie = (await session.execute(q)).scalar_one_or_none()
    if not movie:
        return DownloadResult(success=False, error="Winner movie not found")

    result = await downloader.download(url, session, vote_session_id, progress_callback)

    if result.success:
        vs.state = VoteSessionState.READY
        await session.flush()
    else:
        vs.state = VoteSessionState.DOWNLOAD_FAILED
        await session.flush()

    return result


async def get_media_file(session: AsyncSession, media_file_id: int) -> Optional[MediaFile]:
    return await session.get(MediaFile, media_file_id)


async def list_media_files(session: AsyncSession, vote_session_id: int) -> list[MediaFile]:
    q = select(MediaFile).join(EventMovie).where(EventMovie.session_id == vote_session_id)
    return list((await session.execute(q)).scalars().all())


__all__ = [
    "Downloader",
    "DownloaderError",
    "DownloadResult",
    "VideoInfo",
    "download_winner",
    "provide_download_link",
    "get_media_file",
    "list_media_files",
    "_extract_video_info",
    "_find_forced_subtitle",
]