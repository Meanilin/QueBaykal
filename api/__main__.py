"""HTTP API entrypoint.

Run: uvicorn api.__main__:app --factory
Or:  cinema-api
"""
from __future__ import annotations

import uvicorn

from api import create_app
from core.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "api:create_app",
        factory=True,
        host=settings.api.host,
        port=settings.api.port,
        log_config=None,  # we use structlog
    )


if __name__ == "__main__":
    main()
