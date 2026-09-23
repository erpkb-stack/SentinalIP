"""Agent 2 - Verification.

Weighs the evidence Scout collected and produces a calibrated confidence
score, together with the evidence that argues *against* the finding.
"""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import BaseAgent
from app.constants import (
    CONFIDENCE_DISCLAIMER,
    AgentName,
    FindingKind,
    risk_level_for,
)
from app.database import utcnow
from app.models import AiAgentRun, Case, Evidence


class VerificationAgent(BaseAgent):
    agent_name = AgentName.VERIFICATION.value
    sequence = 2
    charter = (
        "Compare product data, trademark usage, imagery, pricing and seller "
        "behaviour; identify both supporting and contradictory evidence; and "
        "produce a calibrated confidence score. Confidence is not a legal "
        "determination."
    )

    def build_context(self, db: Session, case: Case) -> Dict[str, Any]:
        scout_run = db.execute(
            select(AiAgentRun)
            .where(
                AiAgentRun.case_id == case.id,
                AiAgentRun.agent_name == AgentName.SCOUT.value,
                AiAgentRun.status == "COMPLETE",
            )
            .order_by(AiAgentRun.id.desc())
            .limit(1)
        ).scalars().first()

        signals: List[dict] = []
        observed: dict = {}
        if scout_run and scout_run.output:
            signals = scout_run.output.get("signals", []) or []
            observed = scout_run.output.get("listing", {}) or {}

        evidence_count = db.execute(
            select(Evidence).where(
                Evidence.case_id == case.id, Evidence.is_archived.is_(False)
            )
        ).scalars().all()

        return {
            "task": "verification",
            "case_number": case.case_number,
            "infringement_type": case.infringement_type,
            "signals": signals,
            "listing": observed,
            "evidence_count": len(evidence_count),
            "scout_run_id": scout_run.id if scout_run else None,
        }

    def prompt(self, context: Dict[str, Any]) -> str:
        return (
            "Weigh the collected signals for and against infringement. Produce a "
            "calibrated 0-100 confidence score, state the finding in one sentence, "
            "and list supporting and contradictory evidence separately."
        )

    async def run(
        self, db: Session, case: Case, run: AiAgentRun, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        if not context.get("signals"):
            # Verification has nothing to weigh - say so rather than inventing a score.
            return {
                "summary": "No Scout observations available to verify.",
                "confidence": 0,
                "risk_level": risk_level_for(0),
                "finding": "Verification could not run: no observations on record.",
                "supporting_evidence": [],
                "contradictory_evidence": [],
                "disclaimer": CONFIDENCE_DISCLAIMER,
                "_evidence_created": 0,
            }

        response = await self.provider.analyze(self.prompt(context), context)
        data = response.content

        confidence = float(data.get("confidence", 0))
        risk = data.get("risk_level") or risk_level_for(confidence)

        for item in data.get("supporting_evidence", []):
            self.add_finding(
                db, case, run,
                kind=FindingKind.SUPPORTING.value,
                title=item.get("title", "Supporting evidence"),
                detail=item.get("detail", ""),
                weight=int(item.get("weight", 0)),
                confidence=confidence,
                evidence_refs=item.get("evidence_refs", []),
            )
        for item in data.get("contradictory_evidence", []):
            self.add_finding(
                db, case, run,
                kind=FindingKind.CONTRADICTORY.value,
                title=item.get("title", "Contradictory evidence"),
                detail=item.get("detail", ""),
                weight=int(item.get("weight", 0)),
                confidence=confidence,
                evidence_refs=item.get("evidence_refs", []),
            )

        # Denormalise onto the case for list/filter performance.
        case.ai_confidence = confidence
        case.risk_level = risk
        case.ai_summary = data.get("finding")
        case.analysis_completed_at = utcnow()
        db.add(case)

        return {
            "summary": data.get("summary", ""),
            "confidence": confidence,
            "risk_level": risk,
            "finding": data.get("finding", ""),
            "supporting_evidence": data.get("supporting_evidence", []),
            "contradictory_evidence": data.get("contradictory_evidence", []),
            "method": data.get("method", ""),
            "disclaimer": CONFIDENCE_DISCLAIMER,
            "provider": response.provider,
            "simulated": response.is_simulated,
            "_evidence_created": 0,
        }
