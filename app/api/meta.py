"""Vocabulary endpoints so the frontend never hard-codes enum values."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.config import settings
from app.constants import (
    AGENT_LABELS,
    AUTONOMY_TIERS,
    CONFIDENCE_BANDS,
    CONFIDENCE_DISCLAIMER,
    ENFORCEMENT_ACTION_LABELS,
    EVIDENCE_CATEGORY_LABELS,
    INFRINGEMENT_LABELS,
    ROLE_LABELS,
    CaseStatus,
    EnforcementStatus,
    EvidenceRelevance,
    Priority,
    RiskLevel,
    SlaState,
)
from app.models import User

router = APIRouter(prefix="/api/meta", tags=["meta"])


def _choices(mapping: dict) -> list:
    return [{"value": k, "label": v} for k, v in mapping.items()]


@router.get("")
def vocabulary(user: User = Depends(get_current_user)):
    return {
        "app": {
            "name": settings.app_name,
            "tagline": settings.app_tagline,
            "environment": settings.app_env,
            "simulated_filing": settings.simulated_filing,
        },
        "case_statuses": [
            {"value": s, "label": s.replace("_", " ").title()}
            for s in CaseStatus.values()
        ],
        "enforcement_statuses": [
            {"value": s, "label": s.replace("_", " ").title()}
            for s in EnforcementStatus.values()
        ],
        "infringement_types": _choices(INFRINGEMENT_LABELS),
        "enforcement_actions": _choices(ENFORCEMENT_ACTION_LABELS),
        "evidence_categories": _choices(EVIDENCE_CATEGORY_LABELS),
        "evidence_relevance": [
            {"value": r, "label": r.replace("_", " ").title()}
            for r in EvidenceRelevance.values()
        ],
        "priorities": [
            {"value": p, "label": p.title()} for p in Priority.values()
        ],
        "risk_levels": [
            {"value": r, "label": r.title()} for r in RiskLevel.values()
        ],
        "sla_states": [
            {"value": s, "label": s.replace("_", " ").title()}
            for s in SlaState.values()
        ],
        "roles": _choices(ROLE_LABELS),
        "agents": _choices(AGENT_LABELS),
        "autonomy_tiers": [
            {"tier": t, **meta, "is_available": t <= settings.max_autonomy_tier}
            for t, meta in sorted(AUTONOMY_TIERS.items())
        ],
        "confidence_bands": [
            {"min": lo, "max": hi, "level": level} for lo, hi, level in CONFIDENCE_BANDS
        ],
        "confidence_disclaimer": CONFIDENCE_DISCLAIMER,
        "upload": {
            "max_mb": settings.max_upload_mb,
            "extensions": settings.allowed_upload_extensions,
        },
    }
