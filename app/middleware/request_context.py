"""Attaches a correlation id to every request and logs the outcome."""
from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.logging_config import get_logger, set_correlation_id

logger = get_logger("sentinel.request")


def client_ip(request: Request) -> str:
    """Best-effort client IP.

    NOTE: X-Forwarded-For is only trustworthy behind a proxy you control.
    Configure your reverse proxy to overwrite (not append) this header.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()[:64]
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()[:64]
    return request.client.host if request.client else "unknown"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cid = request.headers.get("X-Correlation-ID") or uuid.uuid4().hex[:16]
        set_correlation_id(cid)
        request.state.correlation_id = cid
        request.state.client_ip = client_ip(request)

        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        response.headers["X-Correlation-ID"] = cid
        if request.url.path.startswith("/api") or response.status_code >= 400:
            logger.info(
                "%s %s -> %s (%dms)",
                request.method, request.url.path, response.status_code, elapsed_ms,
            )
        return response
