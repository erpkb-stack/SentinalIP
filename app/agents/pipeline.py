"""The Sentinel analysis pipeline.

    Create Case -> Scout -> Verification -> Enforcement Strategist
                -> [ HUMAN APPROVAL GATE ] -> Filing & Tracking

Filing & Tracking prepares the notice and then stops. Submission is a separate,
approval-gated operation (`app.services.enforcement.submit_action`).
"""
from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.enforcement_strategist import EnforcementStrategistAgent
from app.agents.filing_tracking import FilingTrackingAgent
from app.agents.scout import ScoutAgent
from app.agents.verification import VerificationAgent
from app.constants import (
    ENFORCEMENT_ACTION_LABELS,
    AgentName,
    AgentRunStatus,
    CaseStatus,
)
from app.database import session_scope, utcnow
from app.logging_config import get_logger
from app.models import AiAgentRun, Case
from app.services import notifications
from app.services.realtime import manager

logger = get_logger(__name__)

AGENT_CLASSES = {
    AgentName.SCOUT.value: ScoutAgent,
    AgentName.VERIFICATION.value: VerificationAgent,
    AgentName.ENFORCEMENT_STRATEGIST.value: EnforcementStrategistAgent,
    AgentName.FILING_TRACKING.value: FilingTrackingAgent,
}

PIPELINE_ORDER = [
    AgentName.SCOUT.value,
    AgentName.VERIFICATION.value,
    AgentName.ENFORCEMENT_STRATEGIST.value,
    AgentName.FILING_TRACKING.value,
]


async def _publish(case: Case, payload: dict) -> None:
    try:
        await manager.publish_case(case.id, case.vendor_id, payload)
    except Exception:  # pragma: no cover - never break the pipeline on a socket
        logger.debug("WebSocket publish failed for case %s", case.id)


async def run_pipeline(
    db: Session,
    case: Case,
    *,
    triggered_by_id: Optional[int] = None,
    request=None,
    agents: Optional[List[str]] = None,
) -> Dict[str, object]:
    """Run the agent chain against `case` using an existing session."""
    order = agents or PIPELINE_ORDER
    results: Dict[str, object] = {}

    case.status = CaseStatus.ANALYZING.value
    case.analysis_started_at = utcnow()
    db.add(case)
    db.commit()

    await _publish(case, {"event": "pipeline.started", "status": case.status})

    for agent_name in order:
        agent = AGENT_CLASSES[agent_name]()
        await _publish(case, {"event": "agent.started", "agent": agent_name})

        result = await agent.execute(
            db, case, triggered_by_id=triggered_by_id, request=request
        )
        db.commit()

        results[agent_name] = {
            "status": result.run.status,
            "confidence": float(result.run.confidence)
            if result.run.confidence is not None else None,
            "summary": result.run.summary,
            "duration_ms": result.run.duration_ms,
        }

        await _publish(case, {
            "event": "agent.finished",
            "agent": agent_name,
            "status": result.run.status,
            "confidence": results[agent_name]["confidence"],
            "summary": result.run.summary,
        })

        if result.run.status == AgentRunStatus.FAILED.value:
            logger.warning(
                "Pipeline halted: %s failed on case %s", agent_name, case.case_number
            )
            case.status = CaseStatus.DRAFT.value
            db.add(case)
            db.commit()
            await _publish(case, {
                "event": "pipeline.failed", "agent": agent_name,
                "message": "Analysis could not be completed. See the agent output.",
            })
            return {"ok": False, "failed_at": agent_name, "results": results}

    db.refresh(case)

    # ---- notifications ----
    notifications.analysis_complete(db, case)
    if case.status == CaseStatus.AWAITING_APPROVAL.value and case.recommended_action:
        notifications.approval_required(
            db, case,
            ENFORCEMENT_ACTION_LABELS.get(case.recommended_action, case.recommended_action),
        )
    db.commit()

    await _publish(case, {
        "event": "pipeline.finished",
        "status": case.status,
        "confidence": float(case.ai_confidence or 0),
        "risk_level": case.risk_level,
        "recommended_action": case.recommended_action,
        "requires_approval": True,
    })

    return {"ok": True, "results": results, "status": case.status}


def run_pipeline_background(case_id: int, triggered_by_id: Optional[int] = None) -> None:
    """Entry point for FastAPI BackgroundTasks.

    Opens its own session because the request's session is already closed by
    the time the background task runs.
    """
    try:
        with session_scope() as db:
            case = db.get(Case, case_id)
            if case is None:
                logger.warning("Pipeline requested for missing case id=%s", case_id)
                return
            asyncio.run(
                run_pipeline(db, case, triggered_by_id=triggered_by_id)
            )
    except Exception:  # pragma: no cover - background tasks must not crash the app
        logger.exception("Background pipeline failed for case id=%s", case_id)


def pipeline_state(db: Session, case: Case) -> List[dict]:
    """The four agent cards shown on the case detail page."""
    runs = db.execute(
        select(AiAgentRun)
        .where(AiAgentRun.case_id == case.id)
        .order_by(AiAgentRun.sequence, AiAgentRun.id)
    ).scalars().all()

    latest: Dict[str, AiAgentRun] = {}
    for run in runs:
        latest[run.agent_name] = run

    cards = []
    for name in PIPELINE_ORDER:
        agent_cls = AGENT_CLASSES[name]
        run = latest.get(name)
        cards.append({
            "agent": name,
            "sequence": agent_cls.sequence,
            "charter": agent_cls.charter,
            "run_id": run.id if run else None,
            "status": run.status if run else AgentRunStatus.PENDING.value,
            "started_at": run.started_at if run else None,
            "completed_at": run.completed_at if run else None,
            "duration_ms": run.duration_ms if run else None,
            "confidence": float(run.confidence) if run and run.confidence is not None else None,
            "evidence_count": run.evidence_count if run else 0,
            "summary": run.summary if run else None,
            "error": run.error if run else None,
            "provider": run.provider if run else None,
        })
    return cards
