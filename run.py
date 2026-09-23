#!/usr/bin/env python3
"""Development launcher:  python run.py"""
from __future__ import annotations

import uvicorn

from app.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8005,
        reload=settings.debug and not settings.is_production,
        log_level=settings.log_level.lower(),
    )
