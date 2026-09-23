"""WebSocket endpoint for live case / agent status.

The socket is authenticated from the session cookie or a `token` query
parameter, and the case is tenant-checked before the connection is accepted.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.auth.security import decode_access_token
from app.config import settings
from app.database import SessionLocal
from app.logging_config import get_logger
from app.models import Case, User
from app.services.realtime import manager

router = APIRouter(tags=["realtime"])
logger = get_logger(__name__)


def _authenticate(token: Optional[str]) -> Optional[User]:
    if not token:
        return None
    try:
        payload = decode_access_token(token)
    except Exception:
        return None
    db = SessionLocal()
    try:
        user = db.get(User, int(payload.get("sub")))
        if user is None or not user.is_active:
            return None
        if int(payload.get("tv", 0)) != int(user.token_version or 0):
            return None
        # Detach a lightweight copy of what we need.
        _ = user.role_code, user.vendor_id
        return user
    except Exception:
        return None
    finally:
        db.close()


@router.websocket("/ws/cases/{case_id}")
async def case_stream(
    websocket: WebSocket,
    case_id: int,
    token: Optional[str] = Query(None),
):
    token = token or websocket.cookies.get(settings.cookie_name)
    user = _authenticate(token)
    if user is None:
        await websocket.close(code=4401)   # unauthorized
        return

    db = SessionLocal()
    try:
        stmt = select(Case).where(Case.id == case_id)
        if not user.is_global:
            stmt = stmt.where(Case.vendor_id == user.vendor_id)
        case = db.execute(stmt).scalars().first()
    finally:
        db.close()

    if case is None:
        await websocket.close(code=4403)   # forbidden / not visible to this tenant
        return

    await manager.connect(
        websocket,
        vendor_id=case.vendor_id,
        user_id=user.id,
        case_id=case.id,
        is_global=user.is_global,
    )
    await websocket.send_json({
        "event": "connected", "case_id": case.id, "status": case.status,
    })

    try:
        while True:
            # Client pings keep the socket warm; nothing client-sent is trusted.
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=45)
            except asyncio.TimeoutError:
                await websocket.send_json({"event": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover
        logger.debug("WebSocket error on case %s", case_id)
    finally:
        await manager.disconnect(websocket)
