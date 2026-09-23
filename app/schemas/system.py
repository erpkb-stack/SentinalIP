from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from app.constants import Priority
from app.schemas.common import ORMModel


class AuditLogOut(ORMModel):
    id: int
    timestamp: dt.datetime
    user_id: Optional[int] = None
    user_email: Optional[str] = None
    role_code: Optional[str] = None
    vendor_id: Optional[int] = None
    vendor_name: Optional[str] = None
    action: str
    object_type: Optional[str] = None
    object_id: Optional[str] = None
    object_label: Optional[str] = None
    previous_value: Optional[Dict[str, Any]] = None
    new_value: Optional[Dict[str, Any]] = None
    detail: Optional[str] = None
    ip_address: Optional[str] = None
    correlation_id: Optional[str] = None
    success: bool = True


class NotificationOut(ORMModel):
    id: int
    notification_type: str
    severity: str
    title: str
    message: Optional[str] = None
    link: Optional[str] = None
    case_id: Optional[int] = None
    is_read: bool = False
    created_at: dt.datetime


class StatCard(BaseModel):
    key: str
    label: str
    value: float
    unit: Optional[str] = None
    hint: Optional[str] = None
    tone: str = "neutral"


class ChartSeries(BaseModel):
    labels: List[str] = []
    values: List[float] = []
    colors: Optional[List[str]] = None


class DashboardResponse(BaseModel):
    scope: str
    vendor_name: Optional[str] = None
    generated_at: dt.datetime
    cards: List[StatCard] = []
    cases_by_status: ChartSeries
    cases_by_marketplace: ChartSeries
    cases_by_infringement: ChartSeries
    confidence_distribution: ChartSeries
    sla_performance: ChartSeries
    enforcement_success: ChartSeries
    cases_over_time: ChartSeries
    recent_cases: List[Dict[str, Any]] = []
    attention: List[Dict[str, Any]] = []


class AgentOpsCard(BaseModel):
    agent: str
    label: str
    charter: str
    status: str
    provider: str
    model: str
    processed_today: int = 0
    processed_total: int = 0
    success_count: int = 0
    failure_count: int = 0
    avg_duration_ms: int = 0
    last_execution: Optional[dt.datetime] = None
    error_count: int = 0
    metric_label: str = "Cases processed today"


class AiOpsResponse(BaseModel):
    provider: str
    model: str
    simulated: bool
    agents: List[AgentOpsCard]
    total_runs: int = 0
    runs_today: int = 0
    generated_at: dt.datetime


class SlaPolicyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=191)
    description: Optional[str] = None
    response_hours: int = Field(default=48, ge=1, le=8760)
    resolution_hours: int = Field(default=168, ge=1, le=8760)
    priority: str = Priority.MEDIUM.value
    at_risk_percent: int = Field(default=75, ge=10, le=99)
    vendor_id: Optional[int] = None
    is_default: bool = False
    is_active: bool = True

    @field_validator("priority")
    @classmethod
    def _priority(cls, v: str) -> str:
        if v not in Priority.values():
            raise ValueError(f"priority must be one of {Priority.values()}")
        return v


class SlaPolicyOut(ORMModel):
    id: int
    name: str
    description: Optional[str] = None
    response_hours: int
    resolution_hours: int
    priority: str
    at_risk_percent: int
    vendor_id: Optional[int] = None
    vendor_name: Optional[str] = None
    is_default: bool = False
    is_active: bool = True
    created_at: dt.datetime


class AutonomyTierOut(BaseModel):
    tier: int
    code: str
    label: str
    description: str
    requires_human_approval: bool = True
    is_available: bool = True
    vendor_count: int = 0


class MarketplaceOut(ORMModel):
    id: int
    code: str
    name: str
    region: Optional[str] = None
    complaint_portal_url: Optional[str] = None
    avg_response_hours: int = 72
    is_active: bool = True
