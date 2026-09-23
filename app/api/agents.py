"""AI Operations - agent fleet health and throughput."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import case as sa_case, func, select
from sqlalchemy.orm import Session

from app.ai import get_provider
from app.agents.pipeline import AGENT_CLASSES, PIPELINE_ORDER
from app.auth import get_current_user, scope_to_vendor
from app.constants import AGENT_LABELS, AgentRunStatus
from app.database import get_db, utcnow
from app.models import AiAgentRun, User
from app.schemas.system import AgentOpsCard, AiOpsResponse

router = APIRouter(prefix="/api/agents", tags=["agents"])

METRIC_LABELS = {
    "SCOUT": "Cases processed today",
    "VERIFICATION": "Cases processed today",
    "ENFORCEMENT_STRATEGIST": "Recommendations today",
    "FILING_TRACKING": "Active cases",
}


@router.get("", response_model=AiOpsResponse)
def ai_operations(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    provider = get_provider()
    since = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    base = scope_to_vendor(select(AiAgentRun), AiAgentRun, user)

    rows = db.execute(
        base.with_only_columns(
            AiAgentRun.agent_name,
            func.count(AiAgentRun.id).label("total"),
            func.sum(
                sa_case((AiAgentRun.started_at >= since, 1), else_=0)
            ).label("today"),
            func.sum(
                sa_case(
                    (AiAgentRun.status.in_([
                        AgentRunStatus.COMPLETE.value,
                        AgentRunStatus.AWAITING_APPROVAL.value,
                    ]), 1),
                    else_=0,
                )
            ).label("succeeded"),
            func.sum(
                sa_case((AiAgentRun.status == AgentRunStatus.FAILED.value, 1), else_=0)
            ).label("failed"),
            func.avg(AiAgentRun.duration_ms).label("avg_ms"),
            func.max(AiAgentRun.started_at).label("last_run"),
        ).group_by(AiAgentRun.agent_name)
    ).all()

    by_agent = {r.agent_name: r for r in rows}

    cards = []
    for name in PIPELINE_ORDER:
        r = by_agent.get(name)
        cls = AGENT_CLASSES[name]
        failures = int(r.failed or 0) if r else 0
        cards.append(AgentOpsCard(
            agent=name,
            label=AGENT_LABELS.get(name, name),
            charter=cls.charter,
            status="Online",
            provider=provider.name,
            model=provider.model,
            processed_today=int(r.today or 0) if r else 0,
            processed_total=int(r.total or 0) if r else 0,
            success_count=int(r.succeeded or 0) if r else 0,
            failure_count=failures,
            avg_duration_ms=int(r.avg_ms or 0) if r else 0,
            last_execution=r.last_run if r else None,
            error_count=failures,
            metric_label=METRIC_LABELS.get(name, "Runs today"),
        ))

    total_runs = sum(c.processed_total for c in cards)
    runs_today = sum(c.processed_today for c in cards)

    return AiOpsResponse(
        provider=provider.name,
        model=provider.model,
        simulated=getattr(provider, "is_simulated", False),
        agents=cards,
        total_runs=total_runs,
        runs_today=runs_today,
        generated_at=utcnow(),
    )


@router.get("/health")
async def agent_health(user: User = Depends(get_current_user)):
    provider = get_provider()
    health = await provider.health()
    return {
        "success": True,
        "provider": health,
        "agents": [
            {
                "agent": name,
                "label": AGENT_LABELS.get(name, name),
                "charter": AGENT_CLASSES[name].charter,
                "sequence": AGENT_CLASSES[name].sequence,
            }
            for name in PIPELINE_ORDER
        ],
        "human_approval_gate": {
            "position": "between ENFORCEMENT_STRATEGIST and any filing",
            "bypassable": False,
            "note": "Filing & Tracking prepares drafts only. No enforcement action "
                    "is submitted without a recorded human approval.",
        },
    }


@router.get("/runs")
def recent_runs(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = 50,
):
    stmt = scope_to_vendor(select(AiAgentRun), AiAgentRun, user)
    rows = db.execute(
        stmt.order_by(AiAgentRun.started_at.desc()).limit(min(limit, 200))
    ).scalars().all()
    return [
        {
            "id": r.id,
            "case_id": r.case_id,
            "agent": r.agent_name,
            "label": AGENT_LABELS.get(r.agent_name, r.agent_name),
            "status": r.status,
            "confidence": float(r.confidence) if r.confidence is not None else None,
            "duration_ms": r.duration_ms,
            "started_at": r.started_at,
            "summary": r.summary,
            "error": r.error,
        }
        for r in rows
    ]
