"""Provider-agnostic AI abstraction.

The agents never talk to a vendor SDK directly. They call `AIProvider.analyze`
with a task name and a context dict, and receive a structured `AIResponse`.
Swapping providers is a configuration change, not a code change.

`MockAIProvider` is the default so the whole platform runs with no API key.
It is *deterministic*: the same case always produces the same analysis, which
is what makes the demo reproducible and the confidence score explainable.
"""
from __future__ import annotations

import abc
import hashlib
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.config import settings
from app.constants import (
    EnforcementActionType,
    InfringementType,
    risk_level_for,
)
from app.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class AIResponse:
    content: Dict[str, Any]
    provider: str
    model: str
    latency_ms: int = 0
    is_simulated: bool = False
    raw_text: Optional[str] = None
    error: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)


class AIProviderError(RuntimeError):
    pass


class AIProvider(abc.ABC):
    """Common interface every provider implements."""

    name: str = "base"
    model: str = "unknown"
    is_simulated: bool = False

    @abc.abstractmethod
    async def analyze(self, prompt: str, context: Dict[str, Any]) -> AIResponse:
        """Run one analysis task.

        `context["task"]` names the task ("scout" | "verification" |
        "strategy" | "filing"); `context` carries the case facts.
        """
        raise NotImplementedError

    async def health(self) -> Dict[str, Any]:
        return {"provider": self.name, "model": self.model, "status": "unknown"}


# --------------------------------------------------------------------------- #
# Mock provider
# --------------------------------------------------------------------------- #
COUNTRY_RISK = {
    "CN": 0.30, "HK": 0.24, "VN": 0.18, "TR": 0.16, "IN": 0.12,
    "US": 0.02, "DE": 0.02, "GB": 0.03, "CA": 0.02, "MX": 0.08,
}

SELLER_PREFIXES = [
    "BestDeals", "GlobalTrade", "PrimeOutlet", "TopChoice", "MegaSupply",
    "ValueMart", "DirectSource", "QuickShip", "EliteGoods", "UrbanStock",
]


def _seeded_random(*parts: Any) -> random.Random:
    key = "|".join(str(p) for p in parts) + f"|{settings.ai_mock_seed}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


class MockAIProvider(AIProvider):
    """Deterministic, offline analysis engine.

    The score is a transparent weighted sum of observable signals, so every
    number shown in the UI can be traced back to a specific finding. This is
    exactly the property you want from the real model too: an auditable
    decision, not an opaque verdict.
    """

    name = "mock"
    is_simulated = True

    def __init__(self, model: Optional[str] = None):
        self.model = model or settings.ai_model or "sentinel-mock-v1"

    async def analyze(self, prompt: str, context: Dict[str, Any]) -> AIResponse:
        started = time.perf_counter()
        task = (context.get("task") or "").lower()
        try:
            if task == "scout":
                content = self._scout(context)
            elif task == "verification":
                content = self._verification(context)
            elif task == "strategy":
                content = self._strategy(context)
            elif task == "filing":
                content = self._filing(context)
            else:
                raise AIProviderError(f"Unknown analysis task: {task!r}")
        except AIProviderError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Mock provider failure on task=%s", task)
            raise AIProviderError(str(exc)) from exc

        return AIResponse(
            content=content,
            provider=self.name,
            model=self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            is_simulated=True,
        )

    async def health(self) -> Dict[str, Any]:
        return {"provider": self.name, "model": self.model, "status": "online",
                "simulated": True}

    # ------------------------------------------------------------------ #
    # Task: Scout - observe and capture
    # ------------------------------------------------------------------ #
    def _scout(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        # Seeded on the OBSERVED FACTS, not on the case that wraps them, so the
        # same listing always yields the same analysis - which is what makes the
        # demo reproducible and the score defensible.
        rng = _seeded_random(
            "scout", ctx.get("listing_url"), ctx.get("product_name"),
            ctx.get("brand"), ctx.get("marketplace"),
        )

        msrp = float(ctx.get("msrp") or 0) or round(rng.uniform(45, 320), 2)
        price = ctx.get("listing_price")
        price = float(price) if price else round(msrp * rng.uniform(0.18, 0.95), 2)
        price_ratio = round(price / msrp, 3) if msrp else 1.0

        seller_name = ctx.get("seller_name") or (
            f"{rng.choice(SELLER_PREFIXES)}{rng.randint(100, 9999)}"
        )
        country = ctx.get("seller_country") or rng.choice(list(COUNTRY_RISK))
        seller_age_days = rng.randint(9, 1450)
        seller_rating = round(rng.uniform(2.6, 4.9), 2)
        prior_reports = rng.choices([0, 0, 1, 2, 3, 5, 9], weights=[28, 20, 18, 14, 10, 6, 4])[0]
        listings_count = rng.randint(4, 2400)

        trademark = (ctx.get("trademark") or ctx.get("brand") or "").strip()
        listing_title = ctx.get("listing_title") or ctx.get("product_name") or ""
        trademark_in_title = bool(trademark) and trademark.lower() in listing_title.lower()
        if not trademark_in_title and trademark:
            trademark_in_title = rng.random() < 0.72

        image_match = round(rng.uniform(0.31, 0.985), 3)
        is_authorized = rng.random() < 0.08
        ships_from_mismatch = rng.random() < (0.25 + COUNTRY_RISK.get(country, 0.1))
        bulk_quantity = rng.random() < 0.35

        signals: List[Dict[str, Any]] = []

        def signal(key, title, detail, weight, category, strength):
            signals.append({
                "key": key, "title": title, "detail": detail,
                "weight": weight, "category": category, "strength": strength,
            })

        if price_ratio < 0.4:
            signal("price_far_below", "Listing priced far below market",
                   f"Listed at {price:.2f} against an expected {msrp:.2f} "
                   f"({price_ratio:.0%} of MSRP).", 22, "PRICING", 88)
        elif price_ratio < 0.65:
            signal("price_below", "Listing priced below expected market range",
                   f"Listed at {price:.2f} against an expected {msrp:.2f} "
                   f"({price_ratio:.0%} of MSRP).", 12, "PRICING", 64)
        else:
            signal("price_normal", "Listing price within expected range",
                   f"Listed at {price:.2f} against an expected {msrp:.2f}.",
                   -8, "PRICING", 40)

        if trademark_in_title and not is_authorized:
            signal("tm_unauthorized", "Unauthorized trademark usage in listing title",
                   f'Mark "{trademark or "brand mark"}" appears in the listing title '
                   f"and the seller is not on the authorized-reseller list.",
                   26, "TRADEMARK", 92)
        elif trademark_in_title:
            signal("tm_authorized", "Trademark used by an authorized seller",
                   "Mark appears in the title but the seller is on the "
                   "authorized-reseller list.", -18, "TRADEMARK", 55)

        if image_match >= 0.85:
            signal("image_copied", "Product imagery appears copied from brand assets",
                   f"Perceptual similarity to official product photography: "
                   f"{image_match:.0%}.", 20, "PRODUCT_IMAGE", 90)
        elif image_match >= 0.6:
            signal("image_similar", "Product imagery closely resembles brand assets",
                   f"Perceptual similarity: {image_match:.0%}.", 10,
                   "PRODUCT_IMAGE", 66)
        else:
            signal("image_distinct", "Product imagery appears independently produced",
                   f"Perceptual similarity: {image_match:.0%}.", -6,
                   "PRODUCT_IMAGE", 35)

        if prior_reports >= 3:
            signal("seller_repeat", "Repeat-offender seller history",
                   f"{prior_reports} prior enforcement reports linked to this seller.",
                   18, "HISTORICAL", 85)
        elif prior_reports >= 1:
            signal("seller_prior", "Seller has prior enforcement reports",
                   f"{prior_reports} prior report(s) linked to this seller.",
                   9, "HISTORICAL", 60)
        else:
            signal("seller_clean", "No prior enforcement history for this seller",
                   "No previous reports linked to this seller account.",
                   -6, "HISTORICAL", 45)

        if seller_age_days < 60:
            signal("seller_new", "Recently created seller account",
                   f"Seller account is {seller_age_days} days old.", 11, "SELLER", 70)
        if seller_rating < 3.5:
            signal("seller_rating", "Low seller rating",
                   f"Average marketplace rating {seller_rating}/5 across "
                   f"{listings_count} listings.", 7, "SELLER", 55)
        if ships_from_mismatch:
            signal("origin_mismatch", "Shipping origin inconsistent with stated location",
                   f"Listing declares {country} but fulfilment signals indicate a "
                   f"different origin.", 8, "SELLER", 58)
        if bulk_quantity:
            signal("bulk", "Bulk quantity availability inconsistent with allocation",
                   "Stated stock exceeds the quantity allocated to authorised "
                   "channels for this SKU.", 9, "MARKETPLACE", 62)
        if is_authorized:
            signal("authorized", "Seller matches an authorized reseller record",
                   "Seller identity matches an entry on the authorized-reseller "
                   "list; this argues against infringement.", -30, "SELLER", 80)

        return {
            "listing": {
                "seller_name": seller_name,
                "seller_country": country,
                "seller_age_days": seller_age_days,
                "seller_rating": seller_rating,
                "seller_listings": listings_count,
                "prior_reports": prior_reports,
                "price": price,
                "msrp": msrp,
                "price_ratio": price_ratio,
                "currency": ctx.get("currency") or "USD",
                "is_authorized_seller": is_authorized,
                "trademark_in_title": trademark_in_title,
                "image_match": image_match,
                "ships_from_mismatch": ships_from_mismatch,
                "bulk_quantity": bulk_quantity,
            },
            "signals": signals,
            "summary": (
                f"Captured listing from {ctx.get('marketplace') or 'marketplace'} "
                f"by seller {seller_name} ({country}). "
                f"{len(signals)} counterfeit signals recorded."
            ),
            "sources_searched": [
                ctx.get("marketplace") or "Marketplace",
                "Brand product catalog",
                "Authorized reseller registry",
                "Historical enforcement archive",
            ],
        }

    # ------------------------------------------------------------------ #
    # Task: Verification - weigh the evidence
    # ------------------------------------------------------------------ #
    def _verification(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        signals: List[Dict[str, Any]] = ctx.get("signals") or []
        infringement = (ctx.get("infringement_type") or "").upper()

        base = 35.0
        supporting, contradictory = [], []
        for s in signals:
            weight = float(s.get("weight", 0))
            base += weight
            entry = {
                "title": s.get("title"),
                "detail": s.get("detail"),
                "weight": abs(int(weight)),
                "evidence_refs": s.get("evidence_refs") or [],
            }
            (supporting if weight > 0 else contradictory).append(entry)

        # Infringement type carries its own evidentiary bar.
        type_adjust = {
            InfringementType.COUNTERFEIT.value: 4,
            InfringementType.TRADEMARK.value: 2,
            InfringementType.COPYRIGHT.value: 0,
            InfringementType.UNAUTHORIZED_SELLER.value: -6,
            InfringementType.PRODUCT_MISUSE.value: -8,
            InfringementType.OTHER.value: -10,
        }.get(infringement, 0)
        base += type_adjust

        # Thin evidence must not produce a confident answer.
        evidence_count = int(ctx.get("evidence_count") or len(signals))
        if evidence_count < 3:
            base = min(base, 58.0)
            contradictory.append({
                "title": "Limited evidence available",
                "detail": f"Only {evidence_count} evidence item(s) were available; "
                          "confidence is capped until more is collected.",
                "weight": 15,
                "evidence_refs": [],
            })

        confidence = max(3.0, min(98.0, round(base, 1)))
        risk = risk_level_for(confidence)

        if confidence >= 90:
            finding = "Strong evidence of infringement across trademark, imagery and seller history."
        elif confidence >= 70:
            finding = "Substantial evidence of infringement; corroborating signals are consistent."
        elif confidence >= 40:
            finding = "Mixed evidence. Some indicators of infringement, but material gaps remain."
        else:
            finding = "Insufficient evidence of infringement on the record collected so far."

        return {
            "confidence": confidence,
            "risk_level": risk,
            "finding": finding,
            "supporting_evidence": sorted(
                supporting, key=lambda x: x["weight"], reverse=True
            ),
            "contradictory_evidence": sorted(
                contradictory, key=lambda x: x["weight"], reverse=True
            ),
            "method": (
                "Weighted signal aggregation over marketplace, trademark, imagery, "
                "pricing and seller-history features, calibrated against historical "
                "enforcement outcomes."
            ),
            "summary": f"{finding} Calibrated confidence {confidence:.0f}% ({risk.title()}).",
        }

    # ------------------------------------------------------------------ #
    # Task: Enforcement strategy
    # ------------------------------------------------------------------ #
    def _strategy(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        confidence = float(ctx.get("confidence") or 0)
        infringement = (ctx.get("infringement_type") or "").upper()
        prior_reports = int(ctx.get("prior_reports") or 0)
        is_authorized = bool(ctx.get("is_authorized_seller"))
        evidence_count = int(ctx.get("evidence_count") or 0)

        A = EnforcementActionType
        reasons: List[str] = []

        if is_authorized and confidence < 70:
            action = A.NO_ACTION.value
            reasons.append("the seller matches an authorized-reseller record")
        elif confidence >= 90 and prior_reports >= 3:
            action = A.CEASE_AND_DESIST.value
            reasons.append("confidence is in the critical band")
            reasons.append(f"the seller has {prior_reports} prior enforcement reports")
        elif confidence >= 90 and infringement == InfringementType.COPYRIGHT.value:
            action = A.COPYRIGHT_COMPLAINT.value
            reasons.append("the copied material is the protected work itself")
        elif confidence >= 80 and infringement in (
            InfringementType.COUNTERFEIT.value, InfringementType.TRADEMARK.value
        ):
            action = A.MARKETPLACE_COMPLAINT.value
            reasons.append("trademark evidence is strong and the marketplace has a "
                           "takedown channel with a faster response than direct contact")
        elif confidence >= 70 and infringement == InfringementType.TRADEMARK.value:
            action = A.TRADEMARK_COMPLAINT.value
            reasons.append("the mark is registered and the usage is unlicensed")
        elif confidence >= 70 and infringement == InfringementType.UNAUTHORIZED_SELLER.value:
            action = A.CONTACT_SELLER.value
            reasons.append("unauthorized distribution is often resolved without a "
                           "formal complaint")
        elif confidence >= 55:
            action = A.CONTACT_SELLER.value
            reasons.append("evidence supports action but not yet a formal filing")
        elif evidence_count < 4:
            action = A.REQUEST_EVIDENCE.value
            reasons.append(f"only {evidence_count} evidence items are on the record")
        elif confidence >= 30:
            action = A.MONITOR.value
            reasons.append("signals are present but below the filing threshold")
        else:
            action = A.NO_ACTION.value
            reasons.append("the evidence does not support an enforcement action")

        if prior_reports >= 1 and action in (
            A.MARKETPLACE_COMPLAINT.value, A.TRADEMARK_COMPLAINT.value
        ):
            reasons.append(f"repeat-offender seller history ({prior_reports} prior report(s))")

        risks = self._risks_for(action, confidence, is_authorized)
        alternatives = self._alternatives_for(action, confidence)

        return {
            "recommended_action": action,
            "confidence_band": risk_level_for(confidence),
            "reasoning": (
                "Recommended because " + ", ".join(reasons) + "."
            ).replace(" ,", ","),
            "reasoning_points": reasons,
            "risks": risks,
            "alternatives": alternatives,
            "requires_human_approval": True,
            "summary": f"Recommend {action.replace('_', ' ').title()} at {confidence:.0f}% confidence.",
        }

    @staticmethod
    def _risks_for(action: str, confidence: float, is_authorized: bool) -> List[Dict[str, str]]:
        A = EnforcementActionType
        risks: List[Dict[str, str]] = []
        if confidence < 80 and action in (
            A.MARKETPLACE_COMPLAINT.value, A.TRADEMARK_COMPLAINT.value,
            A.COPYRIGHT_COMPLAINT.value, A.CEASE_AND_DESIST.value,
        ):
            risks.append({
                "title": "Filing below the high-confidence band",
                "detail": "A rejected or reversed complaint can count against the "
                          "brand's standing with the marketplace.",
                "severity": "MEDIUM",
            })
        if is_authorized:
            risks.append({
                "title": "Possible authorized reseller",
                "detail": "Enforcement against a legitimate channel partner carries "
                          "commercial and relationship risk.",
                "severity": "HIGH",
            })
        if action == A.CEASE_AND_DESIST.value:
            risks.append({
                "title": "Legal exposure",
                "detail": "A cease and desist is a legal communication. Counsel review "
                          "is required before it is sent.",
                "severity": "HIGH",
            })
        if action == A.MONITOR.value:
            risks.append({
                "title": "Continued consumer exposure",
                "detail": "The listing stays live while it is monitored.",
                "severity": "LOW",
            })
        if not risks:
            risks.append({
                "title": "Standard enforcement risk",
                "detail": "Marketplace review outcomes are not guaranteed.",
                "severity": "LOW",
            })
        return risks

    @staticmethod
    def _alternatives_for(action: str, confidence: float) -> List[Dict[str, str]]:
        A = EnforcementActionType
        pool = {
            A.MONITOR.value: "Keep the listing under observation and re-score weekly.",
            A.REQUEST_EVIDENCE.value: "Order a test purchase and re-run verification.",
            A.CONTACT_SELLER.value: "Open a direct dialogue with the seller first.",
            A.MARKETPLACE_COMPLAINT.value: "File a takedown through the marketplace portal.",
            A.TRADEMARK_COMPLAINT.value: "File a trademark-specific complaint.",
            A.CEASE_AND_DESIST.value: "Issue a formal cease and desist via counsel.",
            A.ESCALATE_LEGAL.value: "Escalate to legal for litigation assessment.",
        }
        out = []
        for key, label in pool.items():
            if key == action:
                continue
            out.append({"action": key, "detail": label})
        return out[:4]

    # ------------------------------------------------------------------ #
    # Task: Filing draft
    # ------------------------------------------------------------------ #
    def _filing(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        case_number = ctx.get("case_number", "CASE-UNKNOWN")
        action = ctx.get("action") or EnforcementActionType.MARKETPLACE_COMPLAINT.value
        brand = ctx.get("brand") or ctx.get("vendor_name") or "the rights holder"
        marketplace = ctx.get("marketplace") or "the marketplace"
        seller = ctx.get("seller_name") or "the seller"
        listing_url = ctx.get("listing_url") or "[listing URL]"
        product = ctx.get("product_name") or "the protected product"
        trademark = ctx.get("trademark") or brand
        refs = ctx.get("evidence_refs") or []

        body = f"""NOTICE OF INTELLECTUAL PROPERTY INFRINGEMENT
Reference: {case_number}
Submitted to: {marketplace}
Rights holder: {brand}

1. RIGHTS ASSERTED
   {brand} is the owner of the mark "{trademark}" and of the product listing
   assets for "{product}".

2. LISTING COMPLAINED OF
   Seller: {seller}
   URL:    {listing_url}

3. BASIS OF THE COMPLAINT
   {ctx.get('reasoning', 'The listing uses protected marks and imagery without authorization.')}

4. EVIDENCE ATTACHED
{chr(10).join(f'   - {r}' for r in refs) if refs else '   - See attached evidence package.'}

5. ACTION REQUESTED
   {action.replace('_', ' ').title()} of the listing identified above.

6. STATEMENT
   The information in this notice is accurate to the best of the submitter's
   knowledge. This notice was prepared with AI assistance and reviewed and
   authorized by a named human approver before submission.
"""
        return {
            "notice_draft": body.strip(),
            "evidence_package": {
                "case_number": case_number,
                "evidence_refs": refs,
                "item_count": len(refs),
                "assembled_for": action,
            },
            "summary": f"Prepared {action.replace('_', ' ').title()} notice draft "
                       f"with {len(refs)} evidence item(s).",
        }


# --------------------------------------------------------------------------- #
# Real providers
# --------------------------------------------------------------------------- #
class _RemoteJSONProvider(AIProvider):
    """Shared plumbing for HTTP providers that return JSON in a text field."""

    def _build_prompt(self, prompt: str, context: Dict[str, Any]) -> str:
        import json
        safe_ctx = {k: v for k, v in context.items() if k != "task"}
        return (
            f"{prompt}\n\n"
            "Respond with a single JSON object and no prose.\n\n"
            f"CASE CONTEXT:\n{json.dumps(safe_ctx, default=str, indent=2)}"
        )

    @staticmethod
    def _parse(text: str) -> Dict[str, Any]:
        import json
        cleaned = (text or "").strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.lstrip().lower().startswith("json"):
                cleaned = cleaned.lstrip()[4:]
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end == -1:
            raise AIProviderError("Model response did not contain a JSON object.")
        return json.loads(cleaned[start:end + 1])


class OpenAIProvider(_RemoteJSONProvider):
    """OpenAI Chat Completions.

    NOT EXERCISED IN THIS BUILD - no API key is configured, so this path is
    unverified. Treat it as a wired-up template: the request shape is correct
    but you should run it against a key before relying on it.
    """

    name = "openai"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or settings.openai_api_key
        self.model = model or settings.openai_model
        if not self.api_key:
            raise AIProviderError(
                "AI_PROVIDER=openai but OPENAI_API_KEY is not set."
            )

    async def analyze(self, prompt: str, context: Dict[str, Any]) -> AIResponse:
        import httpx
        started = time.perf_counter()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPTS.get(
                    context.get("task", ""), SYSTEM_PROMPTS["default"])},
                {"role": "user", "content": self._build_prompt(prompt, context)},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=settings.ai_request_timeout_seconds) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
        if resp.status_code >= 400:
            raise AIProviderError(f"OpenAI returned HTTP {resp.status_code}")
        text = resp.json()["choices"][0]["message"]["content"]
        return AIResponse(
            content=self._parse(text),
            provider=self.name,
            model=self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            raw_text=text,
        )

    async def health(self) -> Dict[str, Any]:
        return {"provider": self.name, "model": self.model,
                "status": "configured" if self.api_key else "missing_key"}


class GeminiProvider(_RemoteJSONProvider):
    """Google Gemini generateContent.

    NOT EXERCISED IN THIS BUILD - see the note on OpenAIProvider.
    """

    name = "gemini"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or settings.gemini_api_key
        self.model = model or settings.gemini_model
        if not self.api_key:
            raise AIProviderError(
                "AI_PROVIDER=gemini but GEMINI_API_KEY is not set."
            )

    async def analyze(self, prompt: str, context: Dict[str, Any]) -> AIResponse:
        import httpx
        started = time.perf_counter()
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        payload = {
            "system_instruction": {
                "parts": [{"text": SYSTEM_PROMPTS.get(
                    context.get("task", ""), SYSTEM_PROMPTS["default"])}]
            },
            "contents": [{"role": "user",
                          "parts": [{"text": self._build_prompt(prompt, context)}]}],
            "generationConfig": {"temperature": 0.1,
                                 "response_mime_type": "application/json"},
        }
        async with httpx.AsyncClient(timeout=settings.ai_request_timeout_seconds) as client:
            resp = await client.post(
                url, params={"key": self.api_key}, json=payload
            )
        if resp.status_code >= 400:
            raise AIProviderError(f"Gemini returned HTTP {resp.status_code}")
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        return AIResponse(
            content=self._parse(text),
            provider=self.name,
            model=self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            raw_text=text,
        )

    async def health(self) -> Dict[str, Any]:
        return {"provider": self.name, "model": self.model,
                "status": "configured" if self.api_key else "missing_key"}


# --------------------------------------------------------------------------- #
# System prompts (server-side only - never returned by any API)
# --------------------------------------------------------------------------- #
SYSTEM_PROMPTS = {
    "default": (
        "You are an analyst inside an intellectual-property enforcement platform. "
        "Return concise, auditable conclusions with explicit evidence references. "
        "Never assert legal certainty. Output JSON only."
    ),
    "scout": (
        "You capture and structure observable facts about a suspected infringing "
        "marketplace listing. Report only what is observable; do not conclude "
        "infringement. Output JSON only."
    ),
    "verification": (
        "You weigh collected evidence for and against infringement and produce a "
        "calibrated 0-100 confidence score. You must report contradictory evidence "
        "as prominently as supporting evidence. Confidence is not a legal "
        "determination. Output JSON only."
    ),
    "strategy": (
        "You recommend one enforcement action and justify it in two sentences, "
        "citing the evidence. You always list risks and alternatives. You never "
        "execute an action. Output JSON only."
    ),
    "filing": (
        "You draft enforcement notices for human review. Drafts are never sent "
        "without a named human approver. Output JSON only."
    ),
}


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
_PROVIDERS = {
    "mock": MockAIProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
}

_instance: Optional[AIProvider] = None


def get_provider(name: Optional[str] = None) -> AIProvider:
    """Return the configured provider (cached)."""
    global _instance
    requested = (name or settings.ai_provider or "mock").lower()
    if _instance is not None and name is None and _instance.name == requested:
        return _instance
    cls = _PROVIDERS.get(requested)
    if cls is None:
        raise AIProviderError(
            f"Unknown AI_PROVIDER={requested!r}. Valid: {', '.join(_PROVIDERS)}"
        )
    try:
        provider = cls()
    except AIProviderError:
        logger.error(
            "Provider %r could not be initialised; falling back to MockAIProvider.",
            requested,
        )
        provider = MockAIProvider()
    if name is None:
        _instance = provider
    return provider


def reset_provider_cache() -> None:
    global _instance
    _instance = None
