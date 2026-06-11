"""Entrypoint: `python -m app` (run from the backend/ directory)."""
from __future__ import annotations

import uvicorn

from .config import settings

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port,
                reload=False, log_level="info")
