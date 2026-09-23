"""Agent 1 - Scout.

Finds and captures potentially infringing listings, and records what it saw.
Scout observes; it never concludes infringement.
"""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy.orm import Session

from app.agents.base import BaseAgent
from app.constants import AgentName, EvidenceCategory, EvidenceType, FindingKind
from app.database import utcnow
from app.models import AiAgentRun, Case, Listing


class ScoutAgent(BaseAgent):
    agent_name = AgentName.SCOUT.value
    sequence = 1
    charter = (
        "Search marketplaces and capture listing, seller, pricing, imagery and "
        "timestamp data for suspected infringing listings. Records observations "
        "only - it makes no infringement determination."
    )

    def build_context(self, db: Session, case: Case) -> Dict[str, Any]:
        product = case.product
        listing = case.listing
        return {
            "task": "scout",
            "case_number": case.case_number,
            "vendor_name": case.vendor.name if case.vendor else None,
            "product_name": product.name if product else case.title,
            "brand": product.brand if product else None,
            "trademark": product.trademark if product else None,
            "msrp": float(product.msrp) if product and product.msrp else None,
            "marketplace": case.marketplace.name if case.marketplace else None,
            "listing_url": listing.url if listing else None,
            "listing_title": listing.title if listing else None,
            "listing_price": float(listing.price) if listing and listing.price else None,
            "currency": listing.currency if listing else "USD",
            "seller_name": listing.seller_name if listing else None,
            "seller_country": listing.seller_country if listing else None,
            "infringement_type": case.infringement_type,
        }

    def prompt(self, context: Dict[str, Any]) -> str:
        return (
            "Capture and structure every observable signal about this suspected "
            "infringing listing: seller identity and history, pricing against "
            "expected market value, trademark usage, imagery provenance, "
            "marketplace metadata and timestamps."
        )

    async def run(
        self, db: Session, case: Case, run: AiAgentRun, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        response = await self.provider.analyze(self.prompt(context), context)
        data = response.content
        observed = data.get("listing", {}) or {}
        signals = data.get("signals", []) or []

        # ---- persist what Scout captured back onto the listing ----
        listing = case.listing
        if listing is None:
            listing = Listing(
                vendor_id=case.vendor_id,
                case_id=case.id,
                marketplace_id=case.marketplace_id,
                url=context.get("listing_url") or "about:blank",
            )
            db.add(listing)
            db.flush()
            case.listing_id = listing.id

        listing.seller_name = listing.seller_name or observed.get("seller_name")
        listing.seller_country = listing.seller_country or observed.get("seller_country")
        if listing.price is None and observed.get("price") is not None:
            listing.price = observed["price"]
        listing.captured_data = observed
        listing.captured_at = utcnow()
        db.add(listing)

        # ---- one evidence item per captured signal ----
        created = 0
        for sig in signals:
            category = sig.get("category") or EvidenceCategory.MARKETPLACE.value
            ev = self.add_evidence(
                db, case, run,
                category=category,
                evidence_type=(
                    EvidenceType.IMAGE.value
                    if category == EvidenceCategory.PRODUCT_IMAGE.value
                    else EvidenceType.STRUCTURED.value
                ),
                title=sig.get("title", "Observation"),
                description=sig.get("detail", ""),
                source=f"{context.get('marketplace') or 'Marketplace'} listing capture",
                source_url=context.get("listing_url"),
                strength=int(sig.get("strength", 50)),
                payload={"signal_key": sig.get("key"), "weight": sig.get("weight")},
            )
            sig["evidence_refs"] = [ev.evidence_ref]
            created += 1

            self.add_finding(
                db, case, run,
                kind=FindingKind.SIGNAL.value,
                title=sig.get("title", "Observation"),
                detail=sig.get("detail", ""),
                weight=abs(int(sig.get("weight", 0))),
                evidence_refs=[ev.evidence_ref],
            )

        # ---- the raw capture itself is evidence ----
        capture = self.add_evidence(
            db, case, run,
            category=EvidenceCategory.MARKETPLACE.value,
            evidence_type=EvidenceType.STRUCTURED.value,
            title="Listing capture record",
            description=(
                f"Full listing snapshot captured from "
                f"{context.get('marketplace') or 'the marketplace'} at "
                f"{utcnow().isoformat()}Z."
            ),
            source=context.get("marketplace") or "Marketplace",
            source_url=context.get("listing_url"),
            strength=70,
            payload=observed,
        )
        created += 1

        return {
            "summary": data.get("summary", "Listing captured."),
            "listing": observed,
            "signals": signals,
            "sources_searched": data.get("sources_searched", []),
            "capture_evidence_ref": capture.evidence_ref,
            "provider": response.provider,
            "simulated": response.is_simulated,
            "_evidence_created": created,
        }
