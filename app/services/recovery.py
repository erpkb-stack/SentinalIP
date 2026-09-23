"""Startup recovery.

The agent pipeline runs in-process (FastAPI BackgroundTasks). If the process
stops mid-run - a restart, a crash, Ctrl-C during a demo - the case is left in
ANALYZING and its in-flight agent run in RUNNING, with nothing alive to finish
them. The case then looks permanently "analysing" in the UI.

A freshly started process has no in-flight pipelines by definition, so anything
still marked RUNNING at startup is an orphan and can be safely reset.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import AgentRunStatus, AuditAction, CaseStatus
from app.database import utcnow
from app.logging_config import get_logger
from app.models import AiAgentRun, Case
from app.services import audit

logger = get_logger(__name__)


def recover_orphaned_analyses(db: Session, limit: int = 500) -> int:
    """Reset cases stranded in ANALYZING. Returns how many were recovered."""
    stranded = db.execute(
        select(Case).where(Case.status == CaseStatus.ANALYZING.value).limit(limit)
    ).scalars().all()
    if not stranded:
        return 0

    now = utcnow()
    for case in stranded:
        runs = db.execute(
            select(AiAgentRun).where(
                AiAgentRun.case_id == case.id,
                AiAgentRun.status == AgentRunStatus.RUNNING.value,
            )
        ).scalars().all()
        for run in runs:
            run.status = AgentRunStatus.FAILED.value
            run.error = (
                "Interrupted: the application stopped while this agent was "
                "running. Re-run the analysis."
            )
            run.completed_at = now
            db.add(run)

        # Back to a state the user can act on, without inventing a result.
        case.status = CaseStatus.DRAFT.value
        case.analysis_started_at = None
        db.add(case)

        audit.record(
            db,
            action=AuditAction.STATUS_CHANGED.value,
            object_type="case",
            object_id=case.id,
            object_label=case.case_number,
            vendor_id=case.vendor_id,
            previous_value={"status": CaseStatus.ANALYZING.value},
            new_value={"status": case.status},
            detail="Analysis was interrupted by an application restart; the "
                   "case was returned to draft so it can be re-run.",
            success=False,
        )
        logger.info("Recovered %s from an interrupted analysis.", case.case_number)

    db.commit()
    return len(stranded)
