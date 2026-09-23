"""Agent 4 - Filing & Tracking.

Prepares the notice draft and the evidence package, then STOPS at the human
approval gate. Submission happens only through
`app.services.enforcement.submit_action`, which refuses to run without a
recorded approval.
"""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import BaseAgent
from app.constants import (
    ENFORCEMENT_ACTION_LABELS,
    AgentName,
    AgentRunStatus,
    EnforcementStatus,
    EvidenceCategory,
    EvidenceType,
)
from app.models import AiAgentRun, Case, EnforcementAction, Evidence


class FilingTrackingAgent(BaseAgent):
    agent_name = AgentName.FILING_TRACKING.value
    sequence = 4
    charter = (
        "Prepare takedown notice drafts and evidence packages, maintain filing "
        "records, and track marketplace response, SLA, deadlines and escalation. "
        "It NEVER files an enforcement action without a recorded human approval."
    )

    def build_context(self, db: Session, case: Case) -> Dict[str, Any]:
        action = db.execute(
            select(EnforcementAction)
            .where(
                EnforcementAction.case_id == case.id,
                EnforcementAction.status.in_(
                    [EnforcementStatus.AWAITING_APPROVAL.value,
                     EnforcementStatus.DRAFT.value,
                     EnforcementStatus.APPROVED.value]
                ),
            )
            .order_by(EnforcementAction.id.desc())
            .limit(1)
        ).scalars().first()

        evidence = db.execute(
            select(Evidence).where(
                Evidence.case_id == case.id,
                Evidence.is_archived.is_(False),
                Evidence.relevance != "NOT_RELEVANT",
            )
        ).scalars().all()

        product = case.product
        listing = case.listing
        return {
            "task": "filing",
            "case_number": case.case_number,
            "vendor_name": case.vendor.name if case.vendor else None,
            "brand": product.brand if product else None,
            "trademark": product.trademark if product else None,
            "product_name": product.name if product else case.title,
            "marketplace": case.marketplace.name if case.marketplace else None,
            "seller_name": listing.seller_name if listing else None,
            "listing_url": listing.url if listing else None,
            "action": action.action_type if action else None,
            "reasoning": action.reasoning if action else "",
            "evidence_refs": [e.evidence_ref for e in evidence],
            "enforcement_action_id": action.id if action else None,
        }

    def prompt(self, context: Dict[str, Any]) -> str:
        return (
            "Draft the enforcement notice and assemble the evidence package for "
            "human review. Do not submit anything."
        )

    async def run(
        self, db: Session, case: Case, run: AiAgentRun, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        action_id = context.get("enforcement_action_id")
        if not action_id:
            return {
                "summary": "No enforcement recommendation to prepare a filing for.",
                "_status": AgentRunStatus.SKIPPED.value,
                "_evidence_created": 0,
            }

        response = await self.provider.analyze(self.prompt(context), context)
        data = response.content

        action = db.get(EnforcementAction, action_id)
        action.notice_draft = data.get("notice_draft")
        action.evidence_package = data.get("evidence_package")
        db.add(action)

        # The assembled package is itself an auditable artefact.
        package_ev = self.add_evidence(
            db, case, run,
            category=EvidenceCategory.EXTERNAL_REFERENCE.value,
            evidence_type=EvidenceType.TEXT.value,
            title=f"Evidence package for {action.reference}",
            description=(
                f"Assembled package of {len(context.get('evidence_refs', []))} item(s) "
                f"supporting the recommended "
                f"{ENFORCEMENT_ACTION_LABELS.get(action.action_type, action.action_type)}."
            ),
            source="Filing & Tracking agent",
            strength=60,
            payload=data.get("evidence_package"),
        )

        return {
            "summary": data.get(
                "summary", "Notice draft prepared. Awaiting human approval."
            ),
            "notice_draft": data.get("notice_draft"),
            "evidence_package": data.get("evidence_package"),
            "package_evidence_ref": package_ev.evidence_ref,
            "enforcement_action_id": action.id,
            "enforcement_reference": action.reference,
            "gate": "HUMAN_APPROVAL_REQUIRED",
            "gate_message": (
                "Filing is prepared but NOT submitted. A named human approver must "
                "authorize this action before it can be filed."
            ),
            "is_simulated": True,
            "provider": response.provider,
            # The run deliberately ends in AWAITING_APPROVAL, not COMPLETE.
            "_status": AgentRunStatus.AWAITING_APPROVAL.value,
            "_evidence_created": 1,
        }
