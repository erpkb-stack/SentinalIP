"""Domain vocabulary for Sentinel IP AI.

Stored as plain strings in the database (portable across MySQL/SQLite and
migration-friendly) but referenced through these classes everywhere in code.
"""
from __future__ import annotations

from enum import Enum
from typing import List


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.value

    @classmethod
    def values(cls) -> List[str]:
        return [m.value for m in cls]


# --------------------------------------------------------------------------- #
# Identity & access
# --------------------------------------------------------------------------- #
class RoleCode(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    VENDOR_USER = "VENDOR_USER"


#: Roles that may NEVER be tied to a single vendor - they are platform-wide.
GLOBAL_ROLES = {RoleCode.SUPER_ADMIN.value}
#: Roles that MUST be attached to exactly one vendor.
VENDOR_SCOPED_ROLES = {RoleCode.VENDOR_USER.value}
#: Roles where a vendor is optional. An Admin WITH a vendor is that tenant's
#: administrator and is tenant-scoped like any vendor user; an Admin WITHOUT a
#: vendor is a platform administrator.
OPTIONAL_VENDOR_ROLES = {RoleCode.ADMIN.value}


class Permission(StrEnum):
    # platform
    PLATFORM_VIEW_ALL = "platform:view_all"
    PLATFORM_ANALYTICS = "platform:analytics"
    CONFIG_MANAGE = "config:manage"          # SLA policies, autonomy tiers
    # users
    USER_VIEW = "user:view"
    USER_CREATE = "user:create"
    USER_UPDATE = "user:update"
    USER_DISABLE = "user:disable"
    USER_DELETE = "user:delete"
    # vendors
    VENDOR_VIEW = "vendor:view"
    VENDOR_CREATE = "vendor:create"
    VENDOR_UPDATE = "vendor:update"
    VENDOR_DISABLE = "vendor:disable"
    VENDOR_ASSIGN = "vendor:assign"
    # cases
    CASE_VIEW = "case:view"
    CASE_CREATE = "case:create"
    CASE_UPDATE = "case:update"
    CASE_APPROVE = "case:approve"
    EVIDENCE_UPLOAD = "evidence:upload"
    EVIDENCE_REVIEW = "evidence:review"
    ENFORCEMENT_SUBMIT = "enforcement:submit"
    # audit
    AUDIT_VIEW = "audit:view"
    AUDIT_VIEW_ALL = "audit:view_all"


ROLE_PERMISSIONS = {
    RoleCode.SUPER_ADMIN.value: Permission.values(),
    RoleCode.ADMIN.value: [
        Permission.USER_VIEW.value,
        Permission.USER_CREATE.value,
        Permission.USER_UPDATE.value,
        Permission.USER_DISABLE.value,
        Permission.VENDOR_VIEW.value,
        Permission.VENDOR_CREATE.value,
        Permission.VENDOR_UPDATE.value,
        Permission.VENDOR_ASSIGN.value,
        Permission.CASE_VIEW.value,
        Permission.CASE_CREATE.value,
        Permission.CASE_UPDATE.value,
        Permission.CASE_APPROVE.value,
        Permission.EVIDENCE_UPLOAD.value,
        Permission.EVIDENCE_REVIEW.value,
        Permission.ENFORCEMENT_SUBMIT.value,
        Permission.AUDIT_VIEW.value,
        Permission.PLATFORM_ANALYTICS.value,
    ],
    RoleCode.VENDOR_USER.value: [
        Permission.CASE_VIEW.value,
        Permission.CASE_CREATE.value,
        Permission.CASE_UPDATE.value,
        Permission.CASE_APPROVE.value,
        Permission.EVIDENCE_UPLOAD.value,
        Permission.EVIDENCE_REVIEW.value,
        Permission.AUDIT_VIEW.value,
    ],
}

ROLE_LABELS = {
    RoleCode.SUPER_ADMIN.value: "Super Admin",
    RoleCode.ADMIN.value: "Admin",
    RoleCode.VENDOR_USER.value: "Vendor User",
}


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    PENDING = "PENDING"


class VendorStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    INACTIVE = "INACTIVE"


# --------------------------------------------------------------------------- #
# Autonomy
# --------------------------------------------------------------------------- #
class AutonomyTier:
    MANUAL = 0
    RECOMMENDATION = 1
    DRAFT = 2
    CONTROLLED_AUTOMATION = 3
    AUTONOMOUS = 4


AUTONOMY_TIERS = {
    0: {
        "code": "TIER_0",
        "label": "Tier 0 - Manual",
        "description": "AI only assists the user. No AI-initiated action.",
        "requires_human_approval": True,
    },
    1: {
        "code": "TIER_1",
        "label": "Tier 1 - AI Recommendation",
        "description": "AI investigates and recommends an action. A human must approve.",
        "requires_human_approval": True,
    },
    2: {
        "code": "TIER_2",
        "label": "Tier 2 - AI Draft",
        "description": "AI generates the evidence package and enforcement notice. "
                       "A human must approve before filing.",
        "requires_human_approval": True,
    },
    3: {
        "code": "TIER_3",
        "label": "Tier 3 - Controlled Automation",
        "description": "Approved workflows may automatically perform predefined "
                       "non-legal operational actions. All actions are audit logged. "
                       "Legal/enforcement filings still require human approval.",
        "requires_human_approval": True,
    },
    4: {
        "code": "TIER_4",
        "label": "Tier 4 - Autonomous (reserved)",
        "description": "Reserved for future implementation. Requires explicit "
                       "administrative configuration and is disabled by default.",
        "requires_human_approval": True,
    },
}

#: Operational, NON-LEGAL actions a Tier 3 workflow may take without a fresh
#: human approval. Everything not in this set is an approval-gated action.
TIER3_AUTOMATABLE_ACTIONS = {"MONITOR", "REQUEST_EVIDENCE", "NO_ACTION"}


# --------------------------------------------------------------------------- #
# Cases
# --------------------------------------------------------------------------- #
class CaseStatus(StrEnum):
    DRAFT = "DRAFT"
    ANALYZING = "ANALYZING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    ACTION_TAKEN = "ACTION_TAKEN"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"


OPEN_CASE_STATUSES = [
    CaseStatus.DRAFT.value,
    CaseStatus.ANALYZING.value,
    CaseStatus.AWAITING_APPROVAL.value,
    CaseStatus.APPROVED.value,
    CaseStatus.CHANGES_REQUESTED.value,
    CaseStatus.SUBMITTED.value,
    CaseStatus.UNDER_REVIEW.value,
    CaseStatus.ESCALATED.value,
]

ACTIVE_INVESTIGATION_STATUSES = [
    CaseStatus.ANALYZING.value,
    CaseStatus.AWAITING_APPROVAL.value,
    CaseStatus.APPROVED.value,
    CaseStatus.CHANGES_REQUESTED.value,
    CaseStatus.SUBMITTED.value,
    CaseStatus.UNDER_REVIEW.value,
]

CLOSED_CASE_STATUSES = [
    CaseStatus.RESOLVED.value,
    CaseStatus.CLOSED.value,
    CaseStatus.REJECTED.value,
]


class InfringementType(StrEnum):
    COUNTERFEIT = "COUNTERFEIT"
    TRADEMARK = "TRADEMARK"
    COPYRIGHT = "COPYRIGHT"
    UNAUTHORIZED_SELLER = "UNAUTHORIZED_SELLER"
    PRODUCT_MISUSE = "PRODUCT_MISUSE"
    OTHER = "OTHER"


INFRINGEMENT_LABELS = {
    "COUNTERFEIT": "Counterfeit",
    "TRADEMARK": "Trademark Infringement",
    "COPYRIGHT": "Copyright Infringement",
    "UNAUTHORIZED_SELLER": "Unauthorized Seller",
    "PRODUCT_MISUSE": "Product Misuse",
    "OTHER": "Other",
}


class Priority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# --------------------------------------------------------------------------- #
# AI confidence
# --------------------------------------------------------------------------- #
class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


CONFIDENCE_BANDS = [
    (0, 39, RiskLevel.LOW.value),
    (40, 69, RiskLevel.MEDIUM.value),
    (70, 89, RiskLevel.HIGH.value),
    (90, 100, RiskLevel.CRITICAL.value),
]


def risk_level_for(confidence: float | int | None) -> str:
    """Map a 0-100 calibrated confidence score onto its risk band.

    This is a *confidence* band, not a legal determination.
    """
    if confidence is None:
        return RiskLevel.LOW.value
    c = max(0, min(100, int(round(float(confidence)))))
    for lo, hi, level in CONFIDENCE_BANDS:
        if lo <= c <= hi:
            return level
    return RiskLevel.LOW.value


#: Shown next to every confidence figure in the UI and in API payloads.
CONFIDENCE_DISCLAIMER = (
    "AI confidence is a calibrated model score, not a legal determination of "
    "infringement. All enforcement decisions require human review."
)


# --------------------------------------------------------------------------- #
# Agents
# --------------------------------------------------------------------------- #
class AgentName(StrEnum):
    SCOUT = "SCOUT"
    VERIFICATION = "VERIFICATION"
    ENFORCEMENT_STRATEGIST = "ENFORCEMENT_STRATEGIST"
    FILING_TRACKING = "FILING_TRACKING"


AGENT_LABELS = {
    "SCOUT": "Scout",
    "VERIFICATION": "Verification",
    "ENFORCEMENT_STRATEGIST": "Enforcement Strategist",
    "FILING_TRACKING": "Filing & Tracking",
}

AGENT_SEQUENCE = [
    AgentName.SCOUT.value,
    AgentName.VERIFICATION.value,
    AgentName.ENFORCEMENT_STRATEGIST.value,
    AgentName.FILING_TRACKING.value,
]


class AgentRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class FindingKind(StrEnum):
    SIGNAL = "SIGNAL"                 # raw observation from Scout
    SUPPORTING = "SUPPORTING"         # supports infringement
    CONTRADICTORY = "CONTRADICTORY"   # argues against infringement
    RISK = "RISK"                     # risk of taking the recommended action
    ALTERNATIVE = "ALTERNATIVE"       # alternative course of action


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #
class EvidenceCategory(StrEnum):
    PRODUCT_IMAGE = "PRODUCT_IMAGE"
    TRADEMARK = "TRADEMARK"
    SELLER = "SELLER"
    MARKETPLACE = "MARKETPLACE"
    PRICING = "PRICING"
    HISTORICAL = "HISTORICAL"
    EXTERNAL_REFERENCE = "EXTERNAL_REFERENCE"


EVIDENCE_CATEGORY_LABELS = {
    "PRODUCT_IMAGE": "Product Images",
    "TRADEMARK": "Trademark Evidence",
    "SELLER": "Seller Evidence",
    "MARKETPLACE": "Marketplace Evidence",
    "PRICING": "Pricing Evidence",
    "HISTORICAL": "Historical Evidence",
    "EXTERNAL_REFERENCE": "External References",
}


class EvidenceType(StrEnum):
    IMAGE = "IMAGE"
    SCREENSHOT = "SCREENSHOT"
    PDF = "PDF"
    TEXT = "TEXT"
    CSV = "CSV"
    SPREADSHEET = "SPREADSHEET"
    URL = "URL"
    STRUCTURED = "STRUCTURED"


class EvidenceRelevance(StrEnum):
    UNREVIEWED = "UNREVIEWED"
    RELEVANT = "RELEVANT"
    NOT_RELEVANT = "NOT_RELEVANT"
    DISPUTED = "DISPUTED"


# --------------------------------------------------------------------------- #
# Enforcement
# --------------------------------------------------------------------------- #
class EnforcementActionType(StrEnum):
    MONITOR = "MONITOR"
    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"
    CONTACT_SELLER = "CONTACT_SELLER"
    MARKETPLACE_COMPLAINT = "MARKETPLACE_COMPLAINT"
    TRADEMARK_COMPLAINT = "TRADEMARK_COMPLAINT"
    COPYRIGHT_COMPLAINT = "COPYRIGHT_COMPLAINT"
    CEASE_AND_DESIST = "CEASE_AND_DESIST"
    ESCALATE_LEGAL = "ESCALATE_LEGAL"
    NO_ACTION = "NO_ACTION"


ENFORCEMENT_ACTION_LABELS = {
    "MONITOR": "Monitor",
    "REQUEST_EVIDENCE": "Request Additional Evidence",
    "CONTACT_SELLER": "Contact Seller",
    "MARKETPLACE_COMPLAINT": "Marketplace Takedown",
    "TRADEMARK_COMPLAINT": "Trademark Complaint",
    "COPYRIGHT_COMPLAINT": "Copyright Complaint",
    "CEASE_AND_DESIST": "Cease and Desist",
    "ESCALATE_LEGAL": "Escalate to Legal",
    "NO_ACTION": "No Action",
}

#: Actions with legal weight. These may NEVER be executed without an explicit,
#: recorded human approval, at any autonomy tier.
LEGAL_ACTIONS = {
    "MARKETPLACE_COMPLAINT",
    "TRADEMARK_COMPLAINT",
    "COPYRIGHT_COMPLAINT",
    "CEASE_AND_DESIST",
    "ESCALATE_LEGAL",
    "CONTACT_SELLER",
}


class EnforcementStatus(StrEnum):
    DRAFT = "DRAFT"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    ACTION_TAKEN = "ACTION_TAKEN"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"


ENFORCEMENT_PIPELINE = [
    EnforcementStatus.DRAFT.value,
    EnforcementStatus.AWAITING_APPROVAL.value,
    EnforcementStatus.APPROVED.value,
    EnforcementStatus.SUBMITTED.value,
    EnforcementStatus.UNDER_REVIEW.value,
    EnforcementStatus.RESOLVED.value,
]

SUCCESSFUL_ENFORCEMENT_RESULTS = {"LISTING_REMOVED", "SELLER_SUSPENDED", "SELLER_COMPLIED"}


class ApprovalDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"


# --------------------------------------------------------------------------- #
# SLA
# --------------------------------------------------------------------------- #
class SlaState(StrEnum):
    ON_TRACK = "ON_TRACK"
    AT_RISK = "AT_RISK"
    BREACHED = "BREACHED"
    MET = "MET"


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
class AuditAction(StrEnum):
    LOGIN = "LOGIN"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    CASE_CREATED = "CASE_CREATED"
    CASE_VIEWED = "CASE_VIEWED"
    CASE_UPDATED = "CASE_UPDATED"
    EVIDENCE_UPLOADED = "EVIDENCE_UPLOADED"
    EVIDENCE_REVIEWED = "EVIDENCE_REVIEWED"
    AI_AGENT_EXECUTED = "AI_AGENT_EXECUTED"
    AI_RECOMMENDATION_GENERATED = "AI_RECOMMENDATION_GENERATED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    ENFORCEMENT_FILED = "ENFORCEMENT_FILED"
    STATUS_CHANGED = "STATUS_CHANGED"
    USER_CREATED = "USER_CREATED"
    USER_UPDATED = "USER_UPDATED"
    USER_DISABLED = "USER_DISABLED"
    USER_DELETED = "USER_DELETED"
    VENDOR_CREATED = "VENDOR_CREATED"
    VENDOR_UPDATED = "VENDOR_UPDATED"
    VENDOR_DISABLED = "VENDOR_DISABLED"
    VENDOR_ASSIGNMENT_CHANGED = "VENDOR_ASSIGNMENT_CHANGED"
    CONFIGURATION_CHANGED = "CONFIGURATION_CHANGED"
    ACCESS_DENIED = "ACCESS_DENIED"


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #
class NotificationType(StrEnum):
    NEW_CASE = "NEW_CASE"
    AI_ANALYSIS_COMPLETE = "AI_ANALYSIS_COMPLETE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    SLA_APPROACHING = "SLA_APPROACHING"
    SLA_BREACHED = "SLA_BREACHED"
    ENFORCEMENT_RESPONSE = "ENFORCEMENT_RESPONSE"
    CASE_ESCALATED = "CASE_ESCALATED"


class NotificationSeverity(StrEnum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
