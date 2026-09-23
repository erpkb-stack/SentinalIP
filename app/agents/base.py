"""Common agent contract.

Every agent:
  * opens an `ai_agent_runs` row before it does anything,
  * writes its structured conclusions and evidence into the case,
  * closes the run with a status, duration and (where relevant) a confidence,
  * emits an audit record.

Nothing an agent produces is treated as an authorization to act. Acting is
gated separately in `app.services.enforcement`.
"""
from __future__ import annotations

import abc
import datetime as dt
import time
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.ai import AIProvider, AIProviderError, get_provider
from app.constants import AgentRunStatus, AuditAction
from app.database import utcnow
from app.logging_config import get_correlation_id, get_logger
from app.models import AiAgentRun, AiFinding, Case, Evidence
from app.services import audit
from app.services.references import next_evidence_ref

logger = get_logger(__name__)


class AgentResult:
    def __init__(
        self,
        run: AiAgentRun,
        output: Dict[str, Any],
        *,
        confidence: Optional[float] = None,
        evidence_created: int = 0,
    ):
        self.run = run
        self.output = output
        self.confidence = confidence
        self.evidence_created = evidence_created

    @property
    def ok(self) -> bool:
        return self.run.status in (
            AgentRunStatus.COMPLETE.value, AgentRunStatus.AWAITING_APPROVAL.value
        )


class BaseAgent(abc.ABC):
    """Interface shared by all four Sentinel agents."""

    agent_name: str = "BASE"
    sequence: int = 0
    #: Short human-readable statement of what this agent is allowed to do.
    charter: str = ""

    def __init__(self, provider: Optional[AIProvider] = None):
        self.provider = provider or get_provider()

    # ---------------- to implement ----------------
    @abc.abstractmethod
    def build_context(self, db: Session, case: Case) -> Dict[str, Any]:
        """Assemble the facts this agent needs. No side effects."""

    @abc.abstractmethod
    async def run(
        self, db: Session, case: Case, run: AiAgentRun, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Do the work. Return the structured output to persist."""

    def prompt(self, context: Dict[str, Any]) -> str:
        return f"Perform the {self.agent_name} task for case {context.get('case_number')}."

    # ---------------- orchestration ----------------
    async def execute(
        self,
        db: Session,
        case: Case,
        *,
        triggered_by_id: Optional[int] = None,
        request=None,
    ) -> AgentResult:
        run = AiAgentRun(
            case_id=case.id,
            vendor_id=case.vendor_id,
            agent_name=self.agent_name,
            sequence=self.sequence,
            status=AgentRunStatus.RUNNING.value,
            provider=self.provider.name,
            model=self.provider.model,
            started_at=utcnow(),
            correlation_id=get_correlation_id(),
            triggered_by_id=triggered_by_id,
        )
        db.add(run)
        db.flush()

        started = time.perf_counter()
        try:
            context = self.build_context(db, case)
            output = await self.run(db, case, run, context)

            run.output = output
            run.summary = (output or {}).get("summary")
            if output.get("confidence") is not None:
                run.confidence = float(output["confidence"])
            if output.get("risk_level"):
                run.risk_level = output["risk_level"]
            run.status = output.get("_status", AgentRunStatus.COMPLETE.value)
            run.completed_at = utcnow()
            run.duration_ms = int((time.perf_counter() - started) * 1000)
            run.evidence_count = int(output.get("_evidence_created", 0))
            db.add(run)
            db.flush()

            audit.record(
                db,
                action=AuditAction.AI_AGENT_EXECUTED.value,
                request=request,
                object_type="ai_agent_run",
                object_id=run.id,
                object_label=f"{self.agent_name} on {case.case_number}",
                vendor_id=case.vendor_id,
                new_value={
                    "agent": self.agent_name,
                    "status": run.status,
                    "confidence": float(run.confidence) if run.confidence else None,
                    "duration_ms": run.duration_ms,
                    "provider": self.provider.name,
                    "simulated": getattr(self.provider, "is_simulated", False),
                },
                detail=run.summary,
            )
            return AgentResult(
                run, output,
                confidence=float(run.confidence) if run.confidence is not None else None,
                evidence_created=run.evidence_count,
            )

        except AIProviderError as exc:
            return self._fail(db, run, case, started, f"AI provider error: {exc}", request)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Agent %s failed on case %s", self.agent_name, case.id)
            return self._fail(db, run, case, started, str(exc), request)

    def _fail(self, db, run, case, started, message, request) -> AgentResult:
        run.status = AgentRunStatus.FAILED.value
        run.error = message[:2000]
        run.completed_at = utcnow()
        run.duration_ms = int((time.perf_counter() - started) * 1000)
        run.summary = f"{self.agent_name} failed."
        db.add(run)
        db.flush()
        audit.record(
            db,
            action=AuditAction.AI_AGENT_EXECUTED.value,
            request=request,
            object_type="ai_agent_run",
            object_id=run.id,
            object_label=f"{self.agent_name} on {case.case_number}",
            vendor_id=case.vendor_id,
            new_value={"agent": self.agent_name, "status": "FAILED"},
            detail=message[:500],
            success=False,
        )
        return AgentResult(run, {"error": message})

    # ---------------- helpers ----------------
    def add_evidence(
        self,
        db: Session,
        case: Case,
        run: AiAgentRun,
        *,
        category: str,
        evidence_type: str,
        title: str,
        description: str = "",
        source: str = "",
        source_url: Optional[str] = None,
        strength: int = 50,
        payload: Optional[dict] = None,
        collected_at: Optional[dt.datetime] = None,
    ) -> Evidence:
        from app.auth.security import sha256_text

        ref = next_evidence_ref(db, case.id)
        checksum_input = f"{ref}|{title}|{description}|{source_url or ''}"
        ev = Evidence(
            evidence_ref=ref,
            case_id=case.id,
            vendor_id=case.vendor_id,
            category=category,
            evidence_type=evidence_type,
            title=title[:512],
            description=description,
            source=source[:512] if source else None,
            source_url=source_url[:1024] if source_url else None,
            strength=max(0, min(100, int(strength))),
            checksum=sha256_text(checksum_input),
            collected_by=self.agent_name,
            agent_run_id=run.id,
            related_agent=self.agent_name,
            payload=payload,
            collected_at=collected_at or utcnow(),
        )
        db.add(ev)
        db.flush()
        return ev

    def add_finding(
        self,
        db: Session,
        case: Case,
        run: AiAgentRun,
        *,
        kind: str,
        title: str,
        detail: str = "",
        weight: int = 0,
        confidence: Optional[float] = None,
        evidence_refs: Optional[list] = None,
    ) -> AiFinding:
        finding = AiFinding(
            case_id=case.id,
            vendor_id=case.vendor_id,
            agent_run_id=run.id,
            kind=kind,
            title=title[:512],
            detail=detail,
            weight=int(weight),
            confidence=confidence,
            evidence_refs=evidence_refs or [],
        )
        db.add(finding)
        db.flush()
        return finding
