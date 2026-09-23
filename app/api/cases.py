"""Case management - list, create, inspect, decide."""
from __future__ import annotations

import datetime as dt
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.agents.pipeline import pipeline_state, run_pipeline_background
from app.config import settings
from app.auth import (
    CsrfProtected,
    get_current_user,
    scope_to_vendor,
)
from app.constants import (
    AGENT_LABELS,
    CONFIDENCE_DISCLAIMER,
    ENFORCEMENT_ACTION_LABELS,
    EVIDENCE_CATEGORY_LABELS,
    INFRINGEMENT_LABELS,
    ApprovalDecision,
    AuditAction,
    CaseStatus,
    EvidenceRelevance,
    Priority,
    RiskLevel,
    VendorStatus,
)
from app.database import get_db, utcnow
from app.errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
    VendorAccessDenied,
)
from app.models import (
    AiFinding,
    Approval,
    Case,
    CaseAssignment,
    EnforcementAction,
    Evidence,
    Listing,
    Marketplace,
    Product,
    User,
    Vendor,
)
from app.schemas.cases import (
    AgentCard,
    ApprovalOut,
    ApprovalRequest,
    CaseCreate,
    CaseDetail,
    CaseOut,
    CaseUpdate,
    EnforcementActionOut,
    EvidenceOut,
    FindingOut,
    TimelineStep,
)
from app.schemas.common import Page, SlaView
from app.services import audit, enforcement as enforcement_service, notifications, sla
from app.services.references import next_case_number

router = APIRouter(prefix="/api/cases", tags=["cases"])


# --------------------------------------------------------------------------- #
# Serialization
# --------------------------------------------------------------------------- #
def sla_view(case: Case) -> SlaView:
    return SlaView(**sla.case_sla_view(case))


def serialize_case(case: Case) -> CaseOut:
    listing = case.listing
    return CaseOut(
        id=case.id,
        case_number=case.case_number,
        vendor_id=case.vendor_id,
        vendor_name=case.vendor.name if case.vendor else None,
        title=case.title,
        product_name=case.product.name if case.product else None,
        marketplace=case.marketplace.name if case.marketplace else None,
        seller_name=listing.seller_name if listing else None,
        listing_url=listing.url if listing else None,
        infringement_type=case.infringement_type,
        infringement_label=INFRINGEMENT_LABELS.get(
            case.infringement_type, case.infringement_type
        ),
        status=case.status,
        priority=case.priority,
        ai_confidence=float(case.ai_confidence) if case.ai_confidence is not None else None,
        risk_level=case.risk_level,
        recommended_action=case.recommended_action,
        recommended_action_label=ENFORCEMENT_ACTION_LABELS.get(
            case.recommended_action or "", None
        ),
        autonomy_tier=case.autonomy_tier,
        assigned_to_id=case.assigned_to_id,
        assigned_to_name=case.assigned_to.full_name if case.assigned_to else None,
        sla=sla_view(case),
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


def serialize_evidence(ev: Evidence) -> EvidenceOut:
    return EvidenceOut(
        id=ev.id,
        evidence_ref=ev.evidence_ref,
        case_id=ev.case_id,
        category=ev.category,
        category_label=EVIDENCE_CATEGORY_LABELS.get(ev.category, ev.category),
        evidence_type=ev.evidence_type,
        title=ev.title,
        description=ev.description,
        source=ev.source,
        source_url=ev.source_url,
        strength=ev.strength,
        checksum=ev.checksum,
        file_name=ev.file_name,
        mime_type=ev.mime_type,
        file_size=ev.file_size,
        download_url=(f"/api/evidence/{ev.id}/download" if ev.file_path else None),
        collected_by=ev.collected_by,
        related_agent=ev.related_agent,
        relevance=ev.relevance,
        relevance_note=ev.relevance_note,
        reviewed_at=ev.reviewed_at,
        is_archived=ev.is_archived,
        collected_at=ev.collected_at,
    )


def serialize_action(
    db: Session, action: Optional[EnforcementAction]
) -> Optional[EnforcementActionOut]:
    if action is None:
        return None
    approval = enforcement_service.valid_approval_for(db, action)
    action_sla = None
    if action.submitted_at and action.sla_deadline:
        action_sla = SlaView(**sla.evaluate(
            started_at=action.submitted_at,
            deadline=action.sla_deadline,
            resolved_at=action.response_at,
        ))
    return EnforcementActionOut(
        id=action.id,
        reference=action.reference,
        case_id=action.case_id,
        action_type=action.action_type,
        action_label=ENFORCEMENT_ACTION_LABELS.get(action.action_type, action.action_type),
        status=action.status,
        recommendation_version=action.recommendation_version,
        ai_confidence=float(action.ai_confidence) if action.ai_confidence else None,
        reasoning=action.reasoning,
        supporting_evidence=action.supporting_evidence or [],
        risks=action.risks or [],
        alternatives=action.alternatives or [],
        notice_draft=action.notice_draft,
        requires_approval=action.requires_approval,
        has_valid_approval=approval is not None,
        is_simulated=action.is_simulated,
        submitted_at=action.submitted_at,
        submission_reference=action.submission_reference,
        marketplace_response=action.marketplace_response,
        result=action.result,
        is_escalated=action.is_escalated,
        sla=action_sla,
        created_at=action.created_at,
    )


def serialize_approval(a: Approval) -> ApprovalOut:
    return ApprovalOut(
        id=a.id,
        case_id=a.case_id,
        enforcement_action_id=a.enforcement_action_id,
        decision=a.decision,
        decided_by_email=a.decided_by_email,
        decided_by_role=a.decided_by_role,
        decided_by_name=a.decided_by.full_name if a.decided_by else None,
        decided_at=a.decided_at,
        comments=a.comments,
        reason=a.reason,
        ai_recommendation=a.ai_recommendation,
        ai_confidence=float(a.ai_confidence) if a.ai_confidence else None,
        ai_risk_level=a.ai_risk_level,
        recommendation_version=a.recommendation_version,
        autonomy_tier=a.autonomy_tier,
        evidence_item_count=(a.evidence_snapshot or {}).get("count"),
    )


def load_case(db: Session, case_id: int, user: User) -> Case:
    """The single choke point for loading a case.

    Tenant filter is applied inside the query, so another vendor's case is not
    merely hidden - it is never selected.
    """
    stmt = scope_to_vendor(select(Case).where(Case.id == case_id), Case, user)
    case = db.execute(stmt).scalars().first()
    if case is None:
        # Distinguish "does not exist" from "not yours" for the audit trail only.
        exists = db.execute(select(Case.id).where(Case.id == case_id)).first()
        if exists:
            audit.record(
                db, action=AuditAction.ACCESS_DENIED.value, user=user,
                object_type="case", object_id=case_id,
                detail="Cross-tenant case access attempt", success=False, commit=True,
            )
            raise VendorAccessDenied()
        raise NotFound("Case not found.")
    return case


# --------------------------------------------------------------------------- #
# List / search
# --------------------------------------------------------------------------- #
@router.get("", response_model=Page[CaseOut])
def list_cases(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    q: Optional[str] = None,
    status: Optional[List[str]] = Query(None),
    vendor_id: Optional[int] = None,
    marketplace_id: Optional[int] = None,
    infringement_type: Optional[str] = None,
    priority: Optional[str] = None,
    risk_level: Optional[str] = None,
    sla_state: Optional[str] = None,
    assigned_to_id: Optional[int] = None,
    mine: bool = False,
    confidence_min: Optional[float] = Query(None, ge=0, le=100),
    confidence_max: Optional[float] = Query(None, ge=0, le=100),
    created_from: Optional[dt.date] = None,
    created_to: Optional[dt.date] = None,
    sort: str = "created_at",
    direction: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    stmt = select(Case)
    stmt = scope_to_vendor(stmt, Case, user)      # <-- tenant boundary

    if vendor_id is not None:
        if not user.is_global and vendor_id != user.vendor_id:
            raise VendorAccessDenied()
        stmt = stmt.where(Case.vendor_id == vendor_id)
    if status:
        stmt = stmt.where(Case.status.in_(status))
    if marketplace_id is not None:
        stmt = stmt.where(Case.marketplace_id == marketplace_id)
    if infringement_type:
        stmt = stmt.where(Case.infringement_type == infringement_type)
    if priority:
        stmt = stmt.where(Case.priority == priority)
    if risk_level:
        stmt = stmt.where(Case.risk_level == risk_level)
    if sla_state:
        stmt = stmt.where(Case.sla_state == sla_state)
    if assigned_to_id is not None:
        stmt = stmt.where(Case.assigned_to_id == assigned_to_id)
    if mine:
        stmt = stmt.where(
            or_(Case.assigned_to_id == user.id, Case.created_by_id == user.id)
        )
    if confidence_min is not None:
        stmt = stmt.where(Case.ai_confidence >= confidence_min)
    if confidence_max is not None:
        stmt = stmt.where(Case.ai_confidence <= confidence_max)
    if created_from:
        stmt = stmt.where(Case.created_at >= dt.datetime.combine(created_from, dt.time.min))
    if created_to:
        stmt = stmt.where(Case.created_at <= dt.datetime.combine(created_to, dt.time.max))

    if q:
        like = f"%{q.strip()}%"
        stmt = (
            stmt.outerjoin(Listing, Case.listing_id == Listing.id)
            .outerjoin(Product, Case.product_id == Product.id)
            .outerjoin(Marketplace, Case.marketplace_id == Marketplace.id)
            .where(
                or_(
                    Case.case_number.ilike(like),
                    Case.title.ilike(like),
                    Product.name.ilike(like),
                    Product.sku.ilike(like),
                    Listing.seller_name.ilike(like),
                    Listing.url.ilike(like),
                    Marketplace.name.ilike(like),
                )
            )
        )

    total = db.execute(
        select(func.count()).select_from(stmt.with_only_columns(Case.id).subquery())
    ).scalar_one()

    sort_column = {
        "created_at": Case.created_at,
        "updated_at": Case.updated_at,
        "case_number": Case.case_number,
        "confidence": Case.ai_confidence,
        "status": Case.status,
        "priority": Case.priority,
        "sla": Case.sla_deadline,
    }.get(sort, Case.created_at)
    order = sort_column.desc() if direction.lower() == "desc" else sort_column.asc()

    rows = db.execute(
        stmt.order_by(order).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()

    for case in rows:
        sla.refresh_case_sla(db, case)
    db.commit()

    return Page.build([serialize_case(c) for c in rows], total, page, page_size)


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #
@router.post("", response_model=CaseDetail, status_code=201)
def create_case(
    payload: CaseCreate,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = CsrfProtected,
):
    from app.constants import Permission
    if not user.has_permission(Permission.CASE_CREATE.value):
        raise PermissionDenied()

    # ---- resolve the tenant ----
    if user.is_global:
        if payload.vendor_id is None:
            raise ValidationFailed(
                "Select the vendor this case belongs to.",
                details={"fields": [{"field": "vendor_id",
                                     "message": "Vendor is required."}]},
            )
        vendor = db.get(Vendor, payload.vendor_id)
        if vendor is None:
            raise ValidationFailed("The selected vendor does not exist.")
    else:
        if payload.vendor_id is not None and payload.vendor_id != user.vendor_id:
            raise VendorAccessDenied()
        vendor = user.vendor
    if vendor is None:
        raise ValidationFailed("No vendor could be determined for this case.")
    if vendor.status != VendorStatus.ACTIVE.value:
        raise Conflict(f"{vendor.name} is {vendor.status.lower()}; cases cannot be created.")

    # ---- marketplace ----
    marketplace = None
    if payload.marketplace_id is not None:
        marketplace = db.get(Marketplace, payload.marketplace_id)
        if marketplace is None:
            raise ValidationFailed("The selected marketplace does not exist.")
    elif payload.marketplace_name:
        name = payload.marketplace_name.strip()
        marketplace = db.execute(
            select(Marketplace).where(func.lower(Marketplace.name) == name.lower())
        ).scalars().first()
        if marketplace is None:
            marketplace = Marketplace(
                code=name.upper().replace(" ", "_")[:64], name=name,
            )
            db.add(marketplace)
            db.flush()

    # ---- product (reuse by SKU within the tenant) ----
    product = None
    if payload.product_sku:
        product = db.execute(
            select(Product).where(
                Product.vendor_id == vendor.id, Product.sku == payload.product_sku
            )
        ).scalars().first()
    if product is None:
        product = Product(
            vendor_id=vendor.id,
            name=payload.product_name.strip(),
            sku=payload.product_sku,
            brand=payload.brand,
            trademark=payload.trademark,
            product_url=payload.product_url,
            description=payload.product_description,
            msrp=payload.msrp,
            currency=payload.currency,
            created_by_id=user.id,
        )
        db.add(product)
        db.flush()

    # ---- assignment ----
    assigned_to_id = payload.assigned_to_id or user.id
    assignee = db.get(User, assigned_to_id)
    if assignee is None:
        raise ValidationFailed("The selected assignee does not exist.")
    if not assignee.is_global and assignee.vendor_id != vendor.id:
        raise ValidationFailed("The assignee does not belong to this vendor.")

    policy = sla.resolve_policy(db, vendor.id)
    created_at = utcnow()

    case = Case(
        case_number=next_case_number(db, created_at),
        vendor_id=vendor.id,
        title=f"{payload.product_name.strip()} - "
              f"{INFRINGEMENT_LABELS.get(payload.infringement_type, 'Suspected infringement')}",
        description=payload.description,
        product_id=product.id,
        marketplace_id=marketplace.id if marketplace else None,
        infringement_type=payload.infringement_type,
        status=CaseStatus.DRAFT.value,
        priority=payload.priority,
        autonomy_tier=vendor.autonomy_tier,
        requires_approval=True,
        sla_policy_id=policy.id if policy else None,
        sla_deadline=sla.compute_deadline(created_at, policy, payload.priority),
        assigned_to_id=assigned_to_id,
        created_by_id=user.id,
        created_at=created_at,
    )
    db.add(case)
    db.flush()

    listing = Listing(
        vendor_id=vendor.id,
        case_id=case.id,
        marketplace_id=marketplace.id if marketplace else None,
        title=payload.listing_title,
        url=payload.listing_url,
        seller_name=payload.seller_name,
        seller_url=payload.seller_url,
        seller_country=payload.seller_country,
        price=payload.listing_price,
        currency=payload.currency,
        listing_date=payload.listing_date,
    )
    db.add(listing)
    db.flush()
    case.listing_id = listing.id

    db.add(CaseAssignment(
        case_id=case.id, user_id=assigned_to_id, vendor_id=vendor.id,
        assigned_by_id=user.id, note="Initial assignment",
    ))

    audit.record(
        db, action=AuditAction.CASE_CREATED.value, user=user, request=request,
        object_type="case", object_id=case.id, object_label=case.case_number,
        vendor_id=vendor.id,
        new_value={
            "case_number": case.case_number, "vendor": vendor.name,
            "product": product.name, "marketplace": marketplace.name if marketplace else None,
            "listing_url": listing.url, "infringement_type": case.infringement_type,
            "priority": case.priority, "autonomy_tier": case.autonomy_tier,
        },
    )
    notifications.case_created(db, case)
    db.commit()
    db.refresh(case)

    if payload.run_analysis:
        background.add_task(run_pipeline_background, case.id, user.id)

    return build_detail(db, case, user, analysis_queued=payload.run_analysis)


# --------------------------------------------------------------------------- #
# Detail
# --------------------------------------------------------------------------- #
def build_detail(
    db: Session, case: Case, user: User, analysis_queued: bool = False
) -> CaseDetail:
    base = serialize_case(case).model_dump()

    cards = []
    for card in pipeline_state(db, case):
        cards.append(AgentCard(
            **{**card, "label": AGENT_LABELS.get(card["agent"], card["agent"])}
        ))

    evidence = db.execute(
        select(Evidence)
        .where(Evidence.case_id == case.id)
        .order_by(Evidence.category, Evidence.id)
    ).scalars().all()

    findings_rows = db.execute(
        select(AiFinding).where(AiFinding.case_id == case.id).order_by(
            AiFinding.weight.desc(), AiFinding.id
        )
    ).scalars().all()
    findings: dict = {}
    for f in findings_rows:
        findings.setdefault(f.kind, []).append(FindingOut(
            id=f.id, kind=f.kind, title=f.title, detail=f.detail,
            weight=f.weight,
            confidence=float(f.confidence) if f.confidence is not None else None,
            evidence_refs=f.evidence_refs or [],
        ))

    action = enforcement_service.latest_action_for_case(db, case.id)
    approvals = db.execute(
        select(Approval).where(Approval.case_id == case.id)
        .order_by(Approval.decided_at.desc())
    ).scalars().all()

    product = case.product
    listing = case.listing

    return CaseDetail(
        **base,
        description=case.description,
        ai_summary=case.ai_summary,
        product={
            "id": product.id, "name": product.name, "sku": product.sku,
            "brand": product.brand, "trademark": product.trademark,
            "url": product.product_url, "description": product.description,
            "msrp": float(product.msrp) if product.msrp else None,
            "currency": product.currency,
        } if product else None,
        listing={
            "id": listing.id, "url": listing.url, "title": listing.title,
            "seller_name": listing.seller_name, "seller_url": listing.seller_url,
            "seller_country": listing.seller_country,
            "price": float(listing.price) if listing.price else None,
            "currency": listing.currency,
            "listing_date": listing.listing_date,
            "captured_at": listing.captured_at,
            "captured_data": listing.captured_data or {},
        } if listing else None,
        autonomy=enforcement_service.autonomy_view(case.autonomy_tier),
        agents=cards,
        evidence=[serialize_evidence(e) for e in evidence],
        findings=findings,
        enforcement=serialize_action(db, action),
        enforcement_timeline=[
            TimelineStep(**step) for step in
            enforcement_service.enforcement_timeline(db, case)
        ],
        approvals=[serialize_approval(a) for a in approvals],
        created_by_name=case.created_by.full_name if case.created_by else None,
        analysis_started_at=case.analysis_started_at,
        analysis_completed_at=case.analysis_completed_at,
        confidence_disclaimer=CONFIDENCE_DISCLAIMER,
    )


@router.get("/{case_id}", response_model=CaseDetail)
def get_case(
    case_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    poll: bool = Query(
        False,
        description="Set by the UI's background refresh. Suppresses the "
                    "CASE_VIEWED audit record - a poll is not a human opening "
                    "the case, and auditing it floods the trail.",
    ),
):
    case = load_case(db, case_id, user)
    sla.refresh_case_sla(db, case)
    if not poll:
        audit.record(
            db, action=AuditAction.CASE_VIEWED.value, user=user, request=request,
            object_type="case", object_id=case.id, object_label=case.case_number,
            vendor_id=case.vendor_id,
        )
    db.commit()
    db.refresh(case)
    return build_detail(db, case, user)


@router.put("/{case_id}", response_model=CaseDetail)
def update_case(
    case_id: int,
    payload: CaseUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = CsrfProtected,
):
    from app.constants import Permission
    if not user.has_permission(Permission.CASE_UPDATE.value):
        raise PermissionDenied()

    case = load_case(db, case_id, user)
    data = payload.model_dump(exclude_unset=True)

    before = {
        "title": case.title, "status": case.status, "priority": case.priority,
        "infringement_type": case.infringement_type,
        "assigned_to_id": case.assigned_to_id,
    }

    if "status" in data and data["status"]:
        if data["status"] not in CaseStatus.values():
            raise ValidationFailed(f"status must be one of {CaseStatus.values()}")
        # Status transitions that represent a decision or a filing are NOT
        # writable here - they only happen through the approval / filing APIs.
        protected = {
            CaseStatus.APPROVED.value, CaseStatus.REJECTED.value,
            CaseStatus.SUBMITTED.value, CaseStatus.UNDER_REVIEW.value,
            CaseStatus.RESOLVED.value,
        }
        if data["status"] in protected:
            raise Conflict(
                "This status is set by the approval and filing workflow, "
                "not by editing the case."
            )
        case.status = data["status"]

    if "assigned_to_id" in data and data["assigned_to_id"] is not None:
        assignee = db.get(User, data["assigned_to_id"])
        if assignee is None:
            raise ValidationFailed("The selected assignee does not exist.")
        if not assignee.is_global and assignee.vendor_id != case.vendor_id:
            raise ValidationFailed("The assignee does not belong to this vendor.")
        if assignee.id != case.assigned_to_id:
            db.add(CaseAssignment(
                case_id=case.id, user_id=assignee.id, vendor_id=case.vendor_id,
                assigned_by_id=user.id, note="Reassigned",
            ))
        case.assigned_to_id = assignee.id

    for field in ("title", "description", "infringement_type", "priority"):
        if field in data and data[field] is not None:
            setattr(case, field, data[field])

    if "priority" in data and data["priority"]:
        policy = case.sla_policy
        case.sla_deadline = sla.compute_deadline(case.created_at, policy, case.priority)

    db.add(case)
    db.flush()

    after = {
        "title": case.title, "status": case.status, "priority": case.priority,
        "infringement_type": case.infringement_type,
        "assigned_to_id": case.assigned_to_id,
    }
    prev, new = audit.diff(before, after)
    audit.record(
        db, action=AuditAction.CASE_UPDATED.value, user=user, request=request,
        object_type="case", object_id=case.id, object_label=case.case_number,
        vendor_id=case.vendor_id, previous_value=prev, new_value=new,
    )
    db.commit()
    db.refresh(case)
    return build_detail(db, case, user)


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
@router.post("/{case_id}/analyze")
def analyze_case(
    case_id: int,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = CsrfProtected,
):
    case = load_case(db, case_id, user)
    if case.status == CaseStatus.ANALYZING.value:
        raise Conflict("Analysis is already running for this case.")
    if case.status in (CaseStatus.SUBMITTED.value, CaseStatus.RESOLVED.value,
                       CaseStatus.CLOSED.value):
        raise Conflict("This case has already been filed; re-analysis is not available.")

    background.add_task(run_pipeline_background, case.id, user.id)
    return {
        "success": True,
        "message": "AI analysis started.",
        "case_id": case.id,
        "stream": f"/ws/cases/{case.id}",
    }


@router.get("/{case_id}/agents", response_model=List[AgentCard])
def case_agents(
    case_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    case = load_case(db, case_id, user)
    return [
        AgentCard(**{**card, "label": AGENT_LABELS.get(card["agent"], card["agent"])})
        for card in pipeline_state(db, case)
    ]


@router.get("/{case_id}/agents/{run_id}")
def agent_run_detail(
    case_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.models import AiAgentRun

    case = load_case(db, case_id, user)
    run = db.execute(
        select(AiAgentRun).where(AiAgentRun.id == run_id, AiAgentRun.case_id == case.id)
    ).scalars().first()
    if run is None:
        raise NotFound("Agent run not found.")

    findings = db.execute(
        select(AiFinding).where(AiFinding.agent_run_id == run.id)
        .order_by(AiFinding.weight.desc())
    ).scalars().all()

    output = dict(run.output or {})
    # Never expose internal keys or system prompts.
    for key in list(output):
        if key.startswith("_"):
            output.pop(key)

    return {
        "id": run.id,
        "agent": run.agent_name,
        "label": AGENT_LABELS.get(run.agent_name, run.agent_name),
        "status": run.status,
        "provider": run.provider,
        "model": run.model,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "duration_ms": run.duration_ms,
        "confidence": float(run.confidence) if run.confidence is not None else None,
        "risk_level": run.risk_level,
        "evidence_count": run.evidence_count,
        "summary": run.summary,
        "error": run.error,
        "correlation_id": run.correlation_id,
        "output": output,
        "findings": [
            {
                "id": f.id, "kind": f.kind, "title": f.title, "detail": f.detail,
                "weight": f.weight, "evidence_refs": f.evidence_refs or [],
            }
            for f in findings
        ],
        "disclaimer": CONFIDENCE_DISCLAIMER,
    }


# --------------------------------------------------------------------------- #
# Human approval gate
# --------------------------------------------------------------------------- #
def _decide(
    db: Session, case_id: int, payload: ApprovalRequest, request: Request,
    user: User, decision: str,
) -> dict:
    from app.constants import Permission
    if not user.has_permission(Permission.CASE_APPROVE.value):
        raise PermissionDenied("Your role cannot approve or reject recommendations.")

    case = load_case(db, case_id, user)

    if payload.enforcement_action_id:
        action = db.get(EnforcementAction, payload.enforcement_action_id)
        if action is None or action.case_id != case.id:
            raise NotFound("Enforcement action not found for this case.")
    else:
        action = enforcement_service.open_action_for_case(db, case.id)
    if action is None:
        raise Conflict("There is no AI recommendation awaiting a decision on this case.")

    approval = enforcement_service.record_decision(
        db, case=case, action=action, user=user, decision=decision,
        comments=payload.comments, reason=payload.reason, request=request,
    )
    db.commit()

    # An approval IS the authorization to file, so Filing & Tracking picks the
    # action up immediately. `submit_action` still verifies the approval, so
    # this convenience cannot bypass the gate.
    filing_note = None
    if (
        decision == ApprovalDecision.APPROVED.value
        and settings.auto_file_on_approval
    ):
        try:
            enforcement_service.submit_action(
                db, case=case, action=action, user=user, request=request
            )
            db.commit()
            filing_note = (
                f"Filed as {action.submission_reference}"
                + (" (DEMO / SIMULATED)" if action.is_simulated else "")
            )
        except Exception as exc:
            db.rollback()
            filing_note = f"Approved, but filing did not complete: {exc}"

    db.refresh(case)
    return {
        "success": True,
        "filing": filing_note,
        "message": {
            ApprovalDecision.APPROVED.value: "Recommendation approved. "
                                             "The action is now cleared for filing.",
            ApprovalDecision.REJECTED.value: "Recommendation rejected.",
            ApprovalDecision.CHANGES_REQUESTED.value: "Changes requested. "
                                                      "The recommendation returns to draft.",
        }[decision],
        "approval": serialize_approval(approval).model_dump(),
        "case": serialize_case(case).model_dump(),
    }


@router.post("/{case_id}/approve")
def approve(
    case_id: int, payload: ApprovalRequest, request: Request,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
    _: None = CsrfProtected,
):
    return _decide(db, case_id, payload, request, user, ApprovalDecision.APPROVED.value)


@router.post("/{case_id}/reject")
def reject(
    case_id: int, payload: ApprovalRequest, request: Request,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
    _: None = CsrfProtected,
):
    return _decide(db, case_id, payload, request, user, ApprovalDecision.REJECTED.value)


@router.post("/{case_id}/request-changes")
def request_changes(
    case_id: int, payload: ApprovalRequest, request: Request,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
    _: None = CsrfProtected,
):
    return _decide(
        db, case_id, payload, request, user, ApprovalDecision.CHANGES_REQUESTED.value
    )


@router.get("/{case_id}/approvals", response_model=List[ApprovalOut])
def case_approvals(
    case_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    case = load_case(db, case_id, user)
    rows = db.execute(
        select(Approval).where(Approval.case_id == case.id)
        .order_by(Approval.decided_at.desc())
    ).scalars().all()
    return [serialize_approval(a) for a in rows]


@router.get("/{case_id}/audit")
def case_audit(
    case_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = Query(200, ge=1, le=1000),
):
    from app.models import AuditLog
    from app.schemas.system import AuditLogOut

    case = load_case(db, case_id, user)
    rows = db.execute(
        select(AuditLog)
        .where(AuditLog.vendor_id == case.vendor_id)
        .where(
            or_(
                (AuditLog.object_type == "case") & (AuditLog.object_id == str(case.id)),
                AuditLog.object_label.ilike(f"%{case.case_number}%"),
            )
        )
        .order_by(AuditLog.timestamp.desc())
        .limit(limit)
    ).scalars().all()
    return [AuditLogOut.model_validate(r) for r in rows]
