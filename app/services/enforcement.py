"""The human approval gate and the filing / tracking lifecycle.

This module is a security boundary, not a convenience layer. Two rules are
enforced here and nowhere else can bypass them:

  1. An enforcement action with legal weight can NEVER be submitted without an
     APPROVED `approvals` row that matches the exact recommendation version
     being filed.
  2. Autonomy tiers can widen what runs automatically, but never remove rule 1.
     Tier 3 may auto-execute only the operational, non-legal actions listed in
     `TIER3_AUTOMATABLE_ACTIONS`. Tier 4 is disabled.
"""
from __future__ import annotations

import datetime as dt
import random
from typing import Optional

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import (
    AUTONOMY_TIERS,
    ENFORCEMENT_ACTION_LABELS,
    LEGAL_ACTIONS,
    SUCCESSFUL_ENFORCEMENT_RESULTS,
    TIER3_AUTOMATABLE_ACTIONS,
    ApprovalDecision,
    AuditAction,
    CaseStatus,
    EnforcementStatus,
    SlaState,
)
from app.database import utcnow
from app.errors import ApprovalRequired, Conflict, NotFound, PermissionDenied
from app.logging_config import get_correlation_id, get_logger
from app.models import Approval, Case, EnforcementAction, Evidence, User
from app.services import audit, notifications, sla

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def open_action_for_case(db: Session, case_id: int) -> Optional[EnforcementAction]:
    return db.execute(
        select(EnforcementAction)
        .where(
            EnforcementAction.case_id == case_id,
            EnforcementAction.status.in_([
                EnforcementStatus.DRAFT.value,
                EnforcementStatus.AWAITING_APPROVAL.value,
                EnforcementStatus.APPROVED.value,
            ]),
        )
        .order_by(EnforcementAction.id.desc())
        .limit(1)
    ).scalars().first()


def latest_action_for_case(db: Session, case_id: int) -> Optional[EnforcementAction]:
    return db.execute(
        select(EnforcementAction)
        .where(EnforcementAction.case_id == case_id)
        .order_by(EnforcementAction.id.desc())
        .limit(1)
    ).scalars().first()


def valid_approval_for(db: Session, action: EnforcementAction) -> Optional[Approval]:
    """The approval that authorizes THIS version of THIS action, if any.

    An approval recorded against an earlier recommendation version does not
    authorize a re-generated recommendation - the AI changing its mind requires
    a fresh human decision.
    """
    return db.execute(
        select(Approval)
        .where(
            Approval.enforcement_action_id == action.id,
            Approval.decision == ApprovalDecision.APPROVED.value,
            Approval.recommendation_version == action.recommendation_version,
        )
        .order_by(Approval.id.desc())
        .limit(1)
    ).scalars().first()


def evidence_snapshot(db: Session, case: Case) -> dict:
    """Freeze the evidence set at decision time so an approval is reproducible."""
    items = db.execute(
        select(Evidence).where(Evidence.case_id == case.id)
    ).scalars().all()
    return {
        "captured_at": utcnow().isoformat(),
        "count": len(items),
        "items": [
            {
                "ref": e.evidence_ref,
                "category": e.category,
                "title": e.title,
                "strength": e.strength,
                "relevance": e.relevance,
                "checksum": e.checksum,
                "archived": e.is_archived,
            }
            for e in items
        ],
    }


def requires_human_approval(action_type: str, autonomy_tier: int) -> bool:
    """Does this action need a recorded human decision before it can be filed?

    Legal actions: always yes, at every tier.
    Operational actions: yes below Tier 3; Tier 3 may automate the whitelist.
    """
    if action_type in LEGAL_ACTIONS:
        return True
    if autonomy_tier >= 3 and action_type in TIER3_AUTOMATABLE_ACTIONS:
        return False
    return True


# --------------------------------------------------------------------------- #
# Human decisions
# --------------------------------------------------------------------------- #
def record_decision(
    db: Session,
    *,
    case: Case,
    action: EnforcementAction,
    user: User,
    decision: str,
    comments: Optional[str] = None,
    reason: Optional[str] = None,
    request: Optional[Request] = None,
) -> Approval:
    """Record an immutable human decision on an AI recommendation."""
    if action.case_id != case.id:
        raise NotFound()
    if action.status not in (
        EnforcementStatus.AWAITING_APPROVAL.value, EnforcementStatus.DRAFT.value
    ):
        raise Conflict(
            "This recommendation is no longer awaiting a decision "
            f"(current status: {action.status})."
        )

    if decision == ApprovalDecision.REJECTED.value and not (reason or "").strip():
        from app.errors import ValidationFailed
        raise ValidationFailed("A reason is required when rejecting a recommendation.")
    if decision == ApprovalDecision.CHANGES_REQUESTED.value and not (comments or "").strip():
        from app.errors import ValidationFailed
        raise ValidationFailed("Comments are required when requesting changes.")

    approval = Approval(
        case_id=case.id,
        vendor_id=case.vendor_id,
        enforcement_action_id=action.id,
        decision=decision,
        decided_by_id=user.id,
        decided_by_email=user.email,
        decided_by_role=user.role_code,
        decided_at=utcnow(),
        comments=comments,
        reason=reason,
        ai_recommendation=action.action_type,
        ai_confidence=action.ai_confidence,
        ai_risk_level=case.risk_level,
        recommendation_version=action.recommendation_version,
        evidence_snapshot=evidence_snapshot(db, case),
        autonomy_tier=case.autonomy_tier,
        ip_address=getattr(request.state, "client_ip", None) if request else None,
        correlation_id=get_correlation_id(),
    )
    db.add(approval)
    db.flush()

    previous_status = case.status

    if decision == ApprovalDecision.APPROVED.value:
        action.status = EnforcementStatus.APPROVED.value
        action.approval_id = approval.id
        case.status = CaseStatus.APPROVED.value
        case.approved_at = approval.decided_at
        case.approved_by_id = user.id
        audit_action = AuditAction.APPROVAL_GRANTED.value
    elif decision == ApprovalDecision.REJECTED.value:
        action.status = EnforcementStatus.REJECTED.value
        case.status = CaseStatus.REJECTED.value
        case.closed_at = utcnow()
        audit_action = AuditAction.APPROVAL_REJECTED.value
    else:  # CHANGES_REQUESTED
        action.status = EnforcementStatus.DRAFT.value
        case.status = CaseStatus.CHANGES_REQUESTED.value
        audit_action = AuditAction.CHANGES_REQUESTED.value

    # A human has now decided - the response SLA clock stops.
    sla.stop_clock(case, approval.decided_at)
    sla.refresh_case_sla(db, case)

    db.add(action)
    db.add(case)

    audit.record(
        db,
        action=audit_action,
        user=user,
        request=request,
        object_type="enforcement_action",
        object_id=action.id,
        object_label=f"{action.reference} on {case.case_number}",
        vendor_id=case.vendor_id,
        previous_value={"case_status": previous_status, "action_status":
                        EnforcementStatus.AWAITING_APPROVAL.value},
        new_value={
            "case_status": case.status,
            "action_status": action.status,
            "decision": decision,
            "ai_recommendation": action.action_type,
            "ai_confidence": float(action.ai_confidence or 0),
            "recommendation_version": action.recommendation_version,
            "approval_id": approval.id,
        },
        detail=(comments or reason or "")[:500],
    )
    return approval


# --------------------------------------------------------------------------- #
# Filing - the gate
# --------------------------------------------------------------------------- #
def submit_action(
    db: Session,
    *,
    case: Case,
    action: EnforcementAction,
    user: Optional[User] = None,
    request: Optional[Request] = None,
    automated: bool = False,
) -> EnforcementAction:
    """File an enforcement action.

    Raises `ApprovalRequired` unless a matching human approval exists. This is
    the only path to a SUBMITTED state anywhere in the application.
    """
    if action.case_id != case.id:
        raise NotFound()

    if action.status == EnforcementStatus.SUBMITTED.value:
        raise Conflict("This enforcement action has already been submitted.")

    if case.autonomy_tier >= 4:
        raise PermissionDenied(
            "Tier 4 (Autonomous) is reserved and disabled. "
            "Lower the vendor's autonomy tier to proceed."
        )

    needs_approval = requires_human_approval(action.action_type, case.autonomy_tier)

    if needs_approval:
        approval = valid_approval_for(db, action)
        if approval is None:
            # Refuse, and leave a record of the refusal.
            audit.record(
                db,
                action=AuditAction.ACCESS_DENIED.value,
                user=user,
                request=request,
                object_type="enforcement_action",
                object_id=action.id,
                object_label=f"{action.reference} on {case.case_number}",
                vendor_id=case.vendor_id,
                detail="Filing blocked: no matching human approval on record.",
                new_value={
                    "action_type": action.action_type,
                    "recommendation_version": action.recommendation_version,
                    "autonomy_tier": case.autonomy_tier,
                },
                success=False,
            )
            db.commit()
            raise ApprovalRequired()
        if action.status != EnforcementStatus.APPROVED.value:
            raise Conflict(
                f"Action is in status {action.status}; only APPROVED actions can be filed."
            )
    elif not automated:
        # Non-legal action at Tier 3 filed by a human - fine, but still recorded.
        logger.info(
            "Filing non-legal action %s at tier %s without a fresh approval.",
            action.action_type, case.autonomy_tier,
        )

    now = utcnow()
    action.status = EnforcementStatus.SUBMITTED.value
    action.submitted_at = now
    action.submitted_by_id = user.id if user else None
    action.is_simulated = settings.simulated_filing
    action.submission_reference = (
        f"{'SIM' if settings.simulated_filing else 'REF'}-{case.case_number}-"
        f"{action.reference.split('-')[-1]}"
    )

    policy = case.sla_policy
    resolution_hours = policy.resolution_hours if policy else 168
    action.sla_deadline = now + dt.timedelta(hours=resolution_hours)
    action.sla_state = SlaState.ON_TRACK.value

    case.status = CaseStatus.SUBMITTED.value
    db.add(action)
    db.add(case)

    audit.record(
        db,
        action=AuditAction.ENFORCEMENT_FILED.value,
        user=user,
        request=request,
        object_type="enforcement_action",
        object_id=action.id,
        object_label=f"{action.reference} on {case.case_number}",
        vendor_id=case.vendor_id,
        new_value={
            "action_type": action.action_type,
            "status": action.status,
            "submission_reference": action.submission_reference,
            "simulated": action.is_simulated,
            "approval_id": action.approval_id,
            "automated": automated,
        },
        detail=(
            "DEMO / SIMULATED filing - no external system was contacted."
            if action.is_simulated else "Filing submitted."
        ),
    )
    return action


# --------------------------------------------------------------------------- #
# Tracking
# --------------------------------------------------------------------------- #
MARKETPLACE_OUTCOMES = [
    ("LISTING_REMOVED", "Listing removed by the marketplace.", 0.55),
    ("SELLER_SUSPENDED", "Seller account suspended pending review.", 0.12),
    ("SELLER_COMPLIED", "Seller voluntarily removed the listing.", 0.10),
    ("REJECTED", "Complaint rejected: marketplace requested additional evidence.", 0.15),
    ("NO_RESPONSE", "No response received within the SLA window.", 0.08),
]


def simulate_marketplace_response(
    db: Session,
    *,
    case: Case,
    action: EnforcementAction,
    user: Optional[User] = None,
    request: Optional[Request] = None,
    forced_result: Optional[str] = None,
) -> EnforcementAction:
    """DEMO ONLY. Advances a submitted filing to a marketplace outcome.

    In production this is driven by a marketplace webhook or an inbox poller;
    the state transitions below are the same either way.
    """
    if action.status not in (
        EnforcementStatus.SUBMITTED.value, EnforcementStatus.UNDER_REVIEW.value
    ):
        raise Conflict("Only a submitted filing can receive a marketplace response.")

    if forced_result:
        result = forced_result
        message = next(
            (m for r, m, _ in MARKETPLACE_OUTCOMES if r == result),
            "Marketplace response recorded.",
        )
    else:
        rng = random.Random(f"{action.reference}|{settings.ai_mock_seed}")
        result, message, _ = rng.choices(
            MARKETPLACE_OUTCOMES, weights=[w for *_, w in MARKETPLACE_OUTCOMES]
        )[0]

    now = utcnow()
    previous = action.status
    action.response_at = now
    action.marketplace_response = (
        ("[DEMO / SIMULATED] " if action.is_simulated else "") + message
    )
    action.result = result

    if result in SUCCESSFUL_ENFORCEMENT_RESULTS:
        action.status = EnforcementStatus.RESOLVED.value
        case.status = CaseStatus.RESOLVED.value
        case.closed_at = now
    elif result == "REJECTED":
        action.status = EnforcementStatus.ESCALATED.value
        action.is_escalated = True
        action.escalated_at = now
        case.status = CaseStatus.ESCALATED.value
    else:
        action.status = EnforcementStatus.UNDER_REVIEW.value
        case.status = CaseStatus.UNDER_REVIEW.value

    if action.sla_deadline:
        action.sla_state = (
            SlaState.MET.value if now <= action.sla_deadline else SlaState.BREACHED.value
        )

    db.add(action)
    db.add(case)

    notifications.enforcement_response(db, case, action.marketplace_response or message)
    if action.is_escalated:
        notifications.case_escalated(db, case, message)

    audit.record(
        db,
        action=AuditAction.STATUS_CHANGED.value,
        user=user,
        request=request,
        object_type="enforcement_action",
        object_id=action.id,
        object_label=f"{action.reference} on {case.case_number}",
        vendor_id=case.vendor_id,
        previous_value={"status": previous},
        new_value={"status": action.status, "result": result,
                   "case_status": case.status},
        detail=action.marketplace_response,
    )
    return action


def enforcement_timeline(db: Session, case: Case) -> list:
    """The Draft -> Approved -> Submitted -> Review -> Resolved strip."""
    action = latest_action_for_case(db, case.id)
    approvals = db.execute(
        select(Approval)
        .where(Approval.case_id == case.id)
        .order_by(Approval.decided_at)
    ).scalars().all()
    approved = next(
        (a for a in approvals if a.decision == ApprovalDecision.APPROVED.value), None
    )

    steps = [
        {
            "key": "DRAFT", "label": "Draft",
            "done": action is not None,
            "at": action.created_at if action else None,
            "detail": ENFORCEMENT_ACTION_LABELS.get(
                action.action_type, action.action_type) if action else None,
        },
        {
            "key": "HUMAN_APPROVED", "label": "Human Approved",
            "done": approved is not None,
            "at": approved.decided_at if approved else None,
            "detail": f"Approved by {approved.decided_by_email}" if approved else
                      "Awaiting a named human approver",
        },
        {
            "key": "SUBMITTED", "label": "Submitted",
            "done": bool(action and action.submitted_at),
            "at": action.submitted_at if action else None,
            "detail": action.submission_reference if action else None,
        },
        {
            "key": "UNDER_REVIEW", "label": "Marketplace Review",
            "done": bool(action and action.status in (
                EnforcementStatus.UNDER_REVIEW.value,
                EnforcementStatus.RESOLVED.value,
                EnforcementStatus.ESCALATED.value,
            )),
            "at": action.response_at if action else None,
            "detail": action.marketplace_response if action else None,
        },
        {
            "key": "RESOLVED", "label": "Resolved",
            "done": bool(action and action.status == EnforcementStatus.RESOLVED.value),
            "at": action.response_at if action and
                  action.status == EnforcementStatus.RESOLVED.value else None,
            "detail": action.result if action else None,
        },
    ]
    return steps


def autonomy_view(tier: int) -> dict:
    meta = AUTONOMY_TIERS.get(tier, AUTONOMY_TIERS[1])
    return {"tier": tier, **meta}
