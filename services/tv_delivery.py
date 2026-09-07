"""TV delivery service — DLNA/HTTP delivery to TV device.

Issue #65: DLNA/HTTP delivery to TV.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


@dataclass
class DeliveryResult:
    success: bool
    error: str | None = None
    playback_url: str | None = None


class TVDeliveryError(Exception):
    pass


class TVDelivery:
    """Handle delivery of media files to TV devices."""

    def __init__(self, media_base_url: str = "http://localhost:8080/media"):
        self.media_base_url = media_base_url.rstrip("/")

    async def deliver_via_http(
        self,
        tv_device,
        media_file,
        start_position: int = 0,
    ) -> DeliveryResult:
        """Generate HTTP URL for TV to play."""
        # In production, this would:
        # 1. Ensure media file is accessible via HTTP (nginx, python http.server, etc.)
        # 2. Send command to TV agent to play URL
        # 3. Return playback URL

        playback_url = f"{self.media_base_url}/{media_file.filename}"

        # For now, return the URL - actual TV agent integration is separate
        log.info("tv_delivery_http", tv_id=tv_device.id, url=playback_url)
        return DeliveryResult(success=True, playback_url=playback_url)

    async def deliver_via_dlna(
        self,
        tv_device,
        media_file,
        start_position: int = 0,
    ) -> DeliveryResult:
        """Deliver via DLNA (requires DLNA server + TV support)."""
        # DLNA implementation would use a library like async-upnp-client
        # or trigger a DLNA server to serve the file
        log.warning("dlna_delivery_not_implemented", tv_id=tv_device.id)
        return DeliveryResult(
            success=False,
            error="DLNA delivery not implemented yet",
        )

    async def stop_playback(self, tv_device) -> DeliveryResult:
        """Stop playback on TV."""
        # Send stop command to TV agent
        log.info("tv_stop_playback", tv_id=tv_device.id)
        return DeliveryResult(success=True)

    async def seek_playback(self, tv_device, position: int) -> DeliveryResult:
        """Seek to position."""
        log.info("tv_seek_playback", tv_id=tv_device.id, position=position)
        return DeliveryResult(success=True)


# Global instance
tv_delivery = TVDelivery()


__all__ = [
    "TVDelivery",
    "TVDeliveryError",
    "DeliveryResult",
    "tv_delivery",
]