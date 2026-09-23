"""Agent 3 - Enforcement Strategist.

Recommends one enforcement action and explains why. It creates the action in
DRAFT / AWAITING_APPROVAL state. It never submits anything.
"""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import BaseAgent
from app.constants import (
    ENFORCEMENT_ACTION_LABELS,
    AgentName,
    AuditAction,
    CaseStatus,
    EnforcementStatus,
    FindingKind,
    risk_level_for,
)
from app.models import AiAgentRun, Case, EnforcementAction, Evidence
from app.services import audit
from app.services.references import next_enforcement_ref


class EnforcementStrategistAgent(BaseAgent):
    agent_name = AgentName.ENFORCEMENT_STRATEGIST.value
    sequence = 3
    charter = (
        "Select the single most appropriate enforcement action from the approved "
        "action set, justify it against the evidence, and state its risks and "
        "alternatives. It recommends only - it never executes."
    )

    def build_context(self, db: Session, case: Case) -> Dict[str, Any]:
        verification = db.execute(
            select(AiAgentRun)
            .where(
                AiAgentRun.case_id == case.id,
                AiAgentRun.agent_name == AgentName.VERIFICATION.value,
                AiAgentRun.status == "COMPLETE",
            )
            .order_by(AiAgentRun.id.desc())
            .limit(1)
        ).scalars().first()

        scout = db.execute(
            select(AiAgentRun)
            .where(
                AiAgentRun.case_id == case.id,
                AiAgentRun.agent_name == AgentName.SCOUT.value,
            )
            .order_by(AiAgentRun.id.desc())
            .limit(1)
        ).scalars().first()

        observed = (scout.output or {}).get("listing", {}) if scout else {}
        evidence = db.execute(
            select(Evidence).where(
                Evidence.case_id == case.id, Evidence.is_archived.is_(False)
            )
        ).scalars().all()

        return {
            "task": "strategy",
            "case_number": case.case_number,
            "infringement_type": case.infringement_type,
            "confidence": float(case.ai_confidence or 0),
            "risk_level": case.risk_level,
            "prior_reports": observed.get("prior_reports", 0),
            "is_authorized_seller": observed.get("is_authorized_seller", False),
            "evidence_count": len(evidence),
            "evidence_refs": [e.evidence_ref for e in evidence],
            "autonomy_tier": case.autonomy_tier,
            "verification_run_id": verification.id if verification else None,
        }

    def prompt(self, context: Dict[str, Any]) -> str:
        return (
            "Recommend exactly one enforcement action. Justify it against the "
            "evidence in plain language, and list the risks of taking it and the "
            "alternatives you rejected."
        )

    async def run(
        self, db: Session, case: Case, run: AiAgentRun, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        response = await self.provider.analyze(self.prompt(context), context)
        data = response.content

        action_type = data.get("recommended_action")
        reasoning = data.get("reasoning", "")
        risks = data.get("risks", []) or []
        alternatives = data.get("alternatives", []) or []
        confidence = float(context.get("confidence") or 0)

        for risk in risks:
            self.add_finding(
                db, case, run,
                kind=FindingKind.RISK.value,
                title=risk.get("title", "Risk"),
                detail=risk.get("detail", ""),
                weight={"HIGH": 80, "MEDIUM": 50, "LOW": 20}.get(
                    risk.get("severity", "LOW"), 20
                ),
            )
        for alt in alternatives:
            self.add_finding(
                db, case, run,
                kind=FindingKind.ALTERNATIVE.value,
                title=ENFORCEMENT_ACTION_LABELS.get(
                    alt.get("action", ""), alt.get("action", "Alternative")
                ),
                detail=alt.get("detail", ""),
            )

        # ---- supersede any earlier open recommendation on this case ----
        previous = db.execute(
            select(EnforcementAction).where(
                EnforcementAction.case_id == case.id,
                EnforcementAction.status.in_(
                    [EnforcementStatus.DRAFT.value,
                     EnforcementStatus.AWAITING_APPROVAL.value]
                ),
            )
        ).scalars().all()
        version = 1
        for prev in previous:
            version = max(version, prev.recommendation_version + 1)
            prev.status = EnforcementStatus.CLOSED.value
            db.add(prev)

        action = EnforcementAction(
            reference=next_enforcement_ref(db, case.id),
            case_id=case.id,
            vendor_id=case.vendor_id,
            action_type=action_type,
            status=EnforcementStatus.AWAITING_APPROVAL.value,
            recommended_by_run_id=run.id,
            recommendation_version=version,
            ai_confidence=confidence,
            reasoning=reasoning,
            supporting_evidence=context.get("evidence_refs", []),
            risks=risks,
            alternatives=alternatives,
            requires_approval=True,   # always; see app/services/enforcement.py
            is_simulated=True,
        )
        db.add(action)
        db.flush()

        # ---- move the case to the human approval gate ----
        case.recommended_action = action_type
        case.risk_level = case.risk_level or risk_level_for(confidence)
        case.requires_approval = True
        case.status = CaseStatus.AWAITING_APPROVAL.value
        db.add(case)

        audit.record(
            db,
            action=AuditAction.AI_RECOMMENDATION_GENERATED.value,
            object_type="enforcement_action",
            object_id=action.id,
            object_label=f"{action.reference} on {case.case_number}",
            vendor_id=case.vendor_id,
            new_value={
                "recommended_action": action_type,
                "confidence": confidence,
                "version": version,
                "requires_approval": True,
            },
            detail=reasoning[:500],
        )

        return {
            "summary": data.get("summary", ""),
            "recommended_action": action_type,
            "recommended_action_label": ENFORCEMENT_ACTION_LABELS.get(
                action_type, action_type
            ),
            "confidence": confidence,
            "confidence_band": data.get("confidence_band", risk_level_for(confidence)),
            "reasoning": reasoning,
            "reasoning_points": data.get("reasoning_points", []),
            "risks": risks,
            "alternatives": alternatives,
            "enforcement_action_id": action.id,
            "enforcement_reference": action.reference,
            "requires_human_approval": True,
            "provider": response.provider,
            "simulated": response.is_simulated,
            "_evidence_created": 0,
        }
