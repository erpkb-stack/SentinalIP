"""WebSocket fan-out for live case / agent status.

Single-process broadcaster. Every subscription is tenant-checked at connect
time, and every published payload carries its vendor_id so a socket can never
receive another tenant's event.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket

from app.logging_config import get_logger

logger = get_logger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._by_case: Dict[int, Set[WebSocket]] = defaultdict(set)
        self._by_vendor: Dict[int, Set[WebSocket]] = defaultdict(set)
        self._meta: Dict[WebSocket, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self, websocket: WebSocket, *, vendor_id: int, user_id: int,
        case_id: Optional[int] = None, is_global: bool = False,
    ) -> None:
        await websocket.accept()
        async with self._lock:
            self._meta[websocket] = {
                "vendor_id": vendor_id, "user_id": user_id,
                "case_id": case_id, "is_global": is_global,
            }
            if case_id is not None:
                self._by_case[case_id].add(websocket)
            self._by_vendor[vendor_id].add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            meta = self._meta.pop(websocket, None)
            if not meta:
                return
            if meta.get("case_id") is not None:
                self._by_case[meta["case_id"]].discard(websocket)
            self._by_vendor[meta["vendor_id"]].discard(websocket)

    async def _send(self, sockets: Set[WebSocket], payload: dict) -> None:
        dead = []
        for ws in list(sockets):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)

    async def publish_case(self, case_id: int, vendor_id: int, payload: dict) -> None:
        body = {**payload, "case_id": case_id, "vendor_id": vendor_id}
        async with self._lock:
            targets = set(self._by_case.get(case_id, set()))
        await self._send(targets, body)

    async def publish_vendor(self, vendor_id: int, payload: dict) -> None:
        body = {**payload, "vendor_id": vendor_id}
        async with self._lock:
            targets = set(self._by_vendor.get(vendor_id, set()))
        await self._send(targets, body)

    def connection_count(self) -> int:
        return len(self._meta)


manager = ConnectionManager()


def publish_threadsafe(coro) -> None:
    """Schedule a publish from sync code (background tasks) without blocking."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        # No loop in this thread (e.g. a script); the event is simply not pushed.
        coro.close()
