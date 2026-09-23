from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.constants import (
    ApprovalDecision,
    EvidenceRelevance,
    InfringementType,
    Priority,
)
from app.schemas.common import ORMModel, SlaView


# --------------------------------------------------------------------------- #
# Create case
# --------------------------------------------------------------------------- #
class CaseCreate(BaseModel):
    # ---- product ----
    product_name: str = Field(min_length=2, max_length=255)
    product_sku: Optional[str] = Field(default=None, max_length=128)
    brand: Optional[str] = Field(default=None, max_length=191)
    trademark: Optional[str] = Field(default=None, max_length=191)
    product_url: Optional[str] = Field(default=None, max_length=512)
    product_description: Optional[str] = None
    msrp: Optional[float] = Field(default=None, ge=0)

    # ---- suspected listing ----
    marketplace_id: Optional[int] = None
    marketplace_name: Optional[str] = Field(default=None, max_length=191)
    listing_url: str = Field(min_length=4, max_length=1024)
    listing_title: Optional[str] = Field(default=None, max_length=512)
    seller_name: Optional[str] = Field(default=None, max_length=191)
    seller_url: Optional[str] = Field(default=None, max_length=1024)
    seller_country: Optional[str] = Field(default=None, max_length=96)
    listing_price: Optional[float] = Field(default=None, ge=0)
    currency: str = Field(default="USD", max_length=8)
    listing_date: Optional[dt.datetime] = None

    # ---- classification ----
    infringement_type: str
    priority: str = Priority.MEDIUM.value
    description: Optional[str] = None

    #: Super Admin / Admin must name the vendor; a Vendor User's is implied.
    vendor_id: Optional[int] = None
    assigned_to_id: Optional[int] = None
    run_analysis: bool = True

    @field_validator("infringement_type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in InfringementType.values():
            raise ValueError(f"infringement_type must be one of {InfringementType.values()}")
        return v

    @field_validator("priority")
    @classmethod
    def _priority(cls, v: str) -> str:
        if v not in Priority.values():
            raise ValueError(f"priority must be one of {Priority.values()}")
        return v

    @field_validator("listing_url", "product_url", "seller_url")
    @classmethod
    def _url(cls, v):
        if v and not v.lower().startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v

    @model_validator(mode="after")
    def _marketplace(self):
        if self.marketplace_id is None and not (self.marketplace_name or "").strip():
            raise ValueError("Select a marketplace or provide a marketplace name.")
        return self


class CaseUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=512)
    description: Optional[str] = None
    infringement_type: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    assigned_to_id: Optional[int] = None

    @field_validator("infringement_type")
    @classmethod
    def _type(cls, v):
        if v is not None and v not in InfringementType.values():
            raise ValueError(f"infringement_type must be one of {InfringementType.values()}")
        return v

    @field_validator("priority")
    @classmethod
    def _priority(cls, v):
        if v is not None and v not in Priority.values():
            raise ValueError(f"priority must be one of {Priority.values()}")
        return v


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #
class CaseOut(ORMModel):
    id: int
    case_number: str
    vendor_id: int
    vendor_name: Optional[str] = None
    title: str
    product_name: Optional[str] = None
    marketplace: Optional[str] = None
    seller_name: Optional[str] = None
    listing_url: Optional[str] = None
    infringement_type: str
    infringement_label: Optional[str] = None
    status: str
    priority: str
    ai_confidence: Optional[float] = None
    risk_level: Optional[str] = None
    recommended_action: Optional[str] = None
    recommended_action_label: Optional[str] = None
    autonomy_tier: int = 1
    assigned_to_id: Optional[int] = None
    assigned_to_name: Optional[str] = None
    sla: Optional[SlaView] = None
    created_at: dt.datetime
    updated_at: dt.datetime


class AgentCard(BaseModel):
    agent: str
    label: str = ""
    sequence: int = 0
    charter: str = ""
    run_id: Optional[int] = None
    status: str
    started_at: Optional[dt.datetime] = None
    completed_at: Optional[dt.datetime] = None
    duration_ms: Optional[int] = None
    confidence: Optional[float] = None
    evidence_count: int = 0
    summary: Optional[str] = None
    error: Optional[str] = None
    provider: Optional[str] = None


class EvidenceOut(ORMModel):
    id: int
    evidence_ref: str
    case_id: int
    category: str
    category_label: Optional[str] = None
    evidence_type: str
    title: str
    description: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    strength: int = 50
    checksum: Optional[str] = None
    file_name: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    download_url: Optional[str] = None
    collected_by: str
    related_agent: Optional[str] = None
    relevance: str
    relevance_note: Optional[str] = None
    reviewed_at: Optional[dt.datetime] = None
    is_archived: bool = False
    collected_at: dt.datetime


class EvidenceUrlCreate(BaseModel):
    category: str
    title: str = Field(min_length=2, max_length=512)
    url: str = Field(max_length=1024)
    description: Optional[str] = None
    strength: int = Field(default=50, ge=0, le=100)

    @field_validator("url")
    @classmethod
    def _url(cls, v):
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class EvidenceReviewRequest(BaseModel):
    relevance: str
    note: Optional[str] = None

    @field_validator("relevance")
    @classmethod
    def _rel(cls, v: str) -> str:
        allowed = [
            EvidenceRelevance.RELEVANT.value,
            EvidenceRelevance.NOT_RELEVANT.value,
            EvidenceRelevance.DISPUTED.value,
            EvidenceRelevance.UNREVIEWED.value,
        ]
        if v not in allowed:
            raise ValueError(f"relevance must be one of {allowed}")
        return v


class FindingOut(ORMModel):
    id: int
    kind: str
    title: str
    detail: Optional[str] = None
    weight: int = 0
    confidence: Optional[float] = None
    evidence_refs: List[str] = []


class EnforcementActionOut(ORMModel):
    id: int
    reference: str
    case_id: int
    action_type: str
    action_label: Optional[str] = None
    status: str
    recommendation_version: int = 1
    ai_confidence: Optional[float] = None
    reasoning: Optional[str] = None
    supporting_evidence: List[str] = []
    risks: List[Dict[str, Any]] = []
    alternatives: List[Dict[str, Any]] = []
    notice_draft: Optional[str] = None
    requires_approval: bool = True
    has_valid_approval: bool = False
    is_simulated: bool = True
    submitted_at: Optional[dt.datetime] = None
    submission_reference: Optional[str] = None
    marketplace_response: Optional[str] = None
    result: Optional[str] = None
    is_escalated: bool = False
    sla: Optional[SlaView] = None
    created_at: dt.datetime


class ApprovalRequest(BaseModel):
    comments: Optional[str] = Field(default=None, max_length=4000)
    reason: Optional[str] = Field(default=None, max_length=4000)
    enforcement_action_id: Optional[int] = None


class ApprovalOut(ORMModel):
    id: int
    case_id: int
    enforcement_action_id: Optional[int] = None
    decision: str
    decided_by_email: str
    decided_by_role: str
    decided_by_name: Optional[str] = None
    decided_at: dt.datetime
    comments: Optional[str] = None
    reason: Optional[str] = None
    ai_recommendation: Optional[str] = None
    ai_confidence: Optional[float] = None
    ai_risk_level: Optional[str] = None
    recommendation_version: int = 1
    autonomy_tier: int = 1
    evidence_item_count: Optional[int] = None


class TimelineStep(BaseModel):
    key: str
    label: str
    done: bool = False
    at: Optional[dt.datetime] = None
    detail: Optional[str] = None


class CaseDetail(CaseOut):
    description: Optional[str] = None
    ai_summary: Optional[str] = None
    product: Optional[Dict[str, Any]] = None
    listing: Optional[Dict[str, Any]] = None
    autonomy: Optional[Dict[str, Any]] = None
    agents: List[AgentCard] = []
    evidence: List[EvidenceOut] = []
    findings: Dict[str, List[FindingOut]] = {}
    enforcement: Optional[EnforcementActionOut] = None
    enforcement_timeline: List[TimelineStep] = []
    approvals: List[ApprovalOut] = []
    created_by_name: Optional[str] = None
    analysis_started_at: Optional[dt.datetime] = None
    analysis_completed_at: Optional[dt.datetime] = None
    confidence_disclaimer: Optional[str] = None


class DecisionKind:
    APPROVE = ApprovalDecision.APPROVED.value
    REJECT = ApprovalDecision.REJECTED.value
    CHANGES = ApprovalDecision.CHANGES_REQUESTED.value
