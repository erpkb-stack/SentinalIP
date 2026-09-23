#!/usr/bin/env python3
"""Seed development / demo data.

    python scripts/seed.py            # seed (refuses if data already exists)
    python scripts/seed.py --reset    # wipe and reseed
    python scripts/seed.py --cases 40 # how many demo cases to generate

The demo cases are produced by running the REAL agent pipeline against the
MockAIProvider, so the evidence, findings, confidence scores and enforcement
recommendations you see are genuine outputs of the shipped code - not fixtures.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from app.agents.pipeline import run_pipeline  # noqa: E402
from app.auth.security import hash_password  # noqa: E402
from app.config import settings  # noqa: E402
from app.constants import (  # noqa: E402
    ROLE_LABELS,
    ROLE_PERMISSIONS,
    ApprovalDecision,
    AuditAction,
    CaseStatus,
    InfringementType,
    Priority,
    RoleCode,
    UserStatus,
    VendorStatus,
)
from app.database import Base, SessionLocal, engine, utcnow  # noqa: E402
from app.logging_config import configure_logging, get_logger  # noqa: E402
from app.models import (  # noqa: E402
    AiAgentRun,
    AiFinding,
    Approval,
    AuditLog,
    Case,
    CaseAssignment,
    EnforcementAction,
    Evidence,
    Listing,
    Marketplace,
    Notification,
    Product,
    Role,
    SlaPolicy,
    User,
    UserVendorAssignment,
    Vendor,
)
from app.services import audit, enforcement as enforcement_service, sla  # noqa: E402
from app.services.references import next_case_number  # noqa: E402

configure_logging("INFO")
logger = get_logger("seed")

DEMO_PASSWORD = settings.seed_default_password
rng = random.Random(settings.ai_mock_seed)


# --------------------------------------------------------------------------- #
MARKETPLACES = [
    ("AMAZON", "Amazon Marketplace", "Global", "https://www.amazon.com/report/infringement", 48),
    ("EBAY", "eBay", "Global", "https://www.ebay.com/vero", 72),
    ("ALIEXPRESS", "AliExpress", "APAC", "https://ipp.alibabagroup.com", 120),
    ("ETSY", "Etsy", "North America", "https://www.etsy.com/legal/ip", 96),
    ("WALMART", "Walmart Marketplace", "North America", "https://brandportal.walmart.com", 60),
    ("SHOPEE", "Shopee", "APAC", "https://seller.shopee.com/ip", 120),
    ("MERCADO", "Mercado Libre", "LATAM", "https://www.mercadolibre.com/brandprotection", 96),
    ("WISH", "Wish", "Global", "https://merchant.wish.com/ip", 144),
]

VENDORS = [
    {
        "name": "Acme Consumer Brands",
        "legal_name": "Acme Consumer Brands, Inc.",
        "contact_name": "Dana Whitfield",
        "contact_email": "ip@acme.local",
        "phone": "+1 512 555 0110",
        "country": "United States",
        "website": "https://acme.example.com",
        "industry": "Consumer Electronics",
        "autonomy_tier": 1,
    },
    {
        "name": "Global Retail Group",
        "legal_name": "Global Retail Group PLC",
        "contact_name": "Marcus Adeyemi",
        "contact_email": "brand.protection@global.local",
        "phone": "+44 20 7946 0958",
        "country": "United Kingdom",
        "website": "https://globalretail.example.com",
        "industry": "Retail",
        "autonomy_tier": 2,
    },
    {
        "name": "Texas Outdoor Products",
        "legal_name": "Texas Outdoor Products LLC",
        "contact_name": "Rosa Delgado",
        "contact_email": "legal@txoutdoor.local",
        "phone": "+1 737 555 0142",
        "country": "United States",
        "website": "https://txoutdoor.example.com",
        "industry": "Outdoor & Sporting Goods",
        "autonomy_tier": 1,
    },
    {
        "name": "Demo Brand Corporation",
        "legal_name": "Demo Brand Corporation",
        "contact_name": "Sam Okafor",
        "contact_email": "demo@demobrand.local",
        "phone": "+1 512 555 0177",
        "country": "United States",
        "website": "https://demobrand.example.com",
        "industry": "Apparel & Accessories",
        "autonomy_tier": 1,
    },
]

PRODUCTS = {
    "Acme Consumer Brands": [
        ("Acme AirPulse Pro Earbuds", "ACM-APP-201", "Acme", "ACME AIRPULSE", 179.00),
        ("Acme SoundDock Mini", "ACM-SDM-114", "Acme", "SOUNDDOCK", 89.00),
        ("Acme ChargeCell 20K Power Bank", "ACM-CC-20K", "Acme", "CHARGECELL", 64.50),
        ("Acme VisionCam 4K", "ACM-VC4K-008", "Acme", "VISIONCAM", 249.00),
    ],
    "Global Retail Group": [
        ("Northline Merino Base Layer", "GRG-NML-330", "Northline", "NORTHLINE", 119.00),
        ("Harborlight Cast Iron Skillet", "GRG-HCS-012", "Harborlight", "HARBORLIGHT", 74.00),
        ("Kestrel Commuter Backpack", "GRG-KCB-441", "Kestrel", "KESTREL", 138.00),
    ],
    "Texas Outdoor Products": [
        ("Longhorn Trail 65L Pack", "TXO-LT65-001", "Longhorn Trail", "LONGHORN TRAIL", 219.00),
        ("Hill Country Camp Stove", "TXO-HCS-220", "Hill Country", "HILL COUNTRY", 96.00),
        ("Brazos 40oz Insulated Tumbler", "TXO-BRZ-040", "Brazos", "BRAZOS", 42.00),
    ],
    "Demo Brand Corporation": [
        ("Demo Classic Court Sneaker", "DMB-CCS-100", "Demo", "DEMO CLASSIC", 129.00),
        ("Demo Field Jacket", "DMB-FJ-455", "Demo", "DEMO FIELD", 189.00),
    ],
}

SELLERS = [
    ("PrimeOutlet_Direct", "CN"), ("GlobalTrade8891", "HK"), ("BestDeals_Depot", "US"),
    ("MegaSupply Warehouse", "VN"), ("EliteGoods Import", "TR"), ("UrbanStock Co", "US"),
    ("ValueMart Express", "CN"), ("QuickShip Traders", "IN"), ("TopChoice Retail", "MX"),
    ("DirectSource Ltd", "GB"),
]


# --------------------------------------------------------------------------- #
def reset(db) -> None:
    logger.warning("Deleting all existing data...")
    for model in (
        AuditLog, Notification, Approval, EnforcementAction, AiFinding, AiAgentRun,
        Evidence, CaseAssignment, Case, Listing, Product, UserVendorAssignment,
        User, Vendor, SlaPolicy, Marketplace, Role,
    ):
        db.execute(model.__table__.delete())
    db.commit()


def seed_roles(db) -> dict:
    definitions = [
        (RoleCode.SUPER_ADMIN.value,
         "Full platform control: all vendors, all cases, all configuration.", False),
        (RoleCode.ADMIN.value,
         "Administers users and vendors. Platform-wide when unassigned; scoped to "
         "one tenant when a vendor is set.", False),
        (RoleCode.VENDOR_USER.value,
         "Works cases for exactly one vendor. Sees nothing outside it.", True),
    ]
    roles = {}
    for code, description, requires_vendor in definitions:
        role = db.execute(select(Role).where(Role.code == code)).scalars().first()
        if role is None:
            role = Role(code=code, name=ROLE_LABELS[code])
            db.add(role)
        role.name = ROLE_LABELS[code]
        role.description = description
        role.permissions = ROLE_PERMISSIONS[code]
        role.requires_vendor = requires_vendor
        role.is_system = True
        roles[code] = role
    db.flush()
    logger.info("Roles: %s", ", ".join(roles))
    return roles


def seed_marketplaces(db) -> dict:
    out = {}
    for code, name, region, portal, hours in MARKETPLACES:
        mp = db.execute(select(Marketplace).where(Marketplace.code == code)).scalars().first()
        if mp is None:
            mp = Marketplace(code=code, name=name)
            db.add(mp)
        mp.name, mp.region = name, region
        mp.complaint_portal_url, mp.avg_response_hours = portal, hours
        mp.is_active = True
        out[code] = mp
    db.flush()
    logger.info("Marketplaces: %s", len(out))
    return out


def seed_sla_policies(db) -> dict:
    definitions = [
        ("Standard Enforcement SLA",
         "Default policy: 48h to a human decision, 7 days for marketplace resolution.",
         48, 168, Priority.MEDIUM.value, 75, True),
        ("Expedited - Critical Brands",
         "12h decision window for high-volume or safety-critical products.",
         12, 72, Priority.HIGH.value, 60, False),
        ("Extended Review",
         "96h decision window for complex or low-volume matters.",
         96, 336, Priority.LOW.value, 85, False),
    ]
    out = {}
    for name, desc, resp, res, prio, at_risk, is_default in definitions:
        p = db.execute(
            select(SlaPolicy).where(SlaPolicy.name == name, SlaPolicy.vendor_id.is_(None))
        ).scalars().first()
        if p is None:
            p = SlaPolicy(name=name)
            db.add(p)
        p.description, p.response_hours, p.resolution_hours = desc, resp, res
        p.priority, p.at_risk_percent, p.is_default = prio, at_risk, is_default
        p.is_active = True
        out[name] = p
    db.flush()
    logger.info("SLA policies: %s", len(out))
    return out


def seed_vendors(db, policies: dict) -> dict:
    out = {}
    for spec in VENDORS:
        v = db.execute(select(Vendor).where(Vendor.name == spec["name"])).scalars().first()
        if v is None:
            v = Vendor(name=spec["name"])
            db.add(v)
        for field, value in spec.items():
            setattr(v, field, value)
        v.status = VendorStatus.ACTIVE.value
        v.sla_policy_id = policies[
            "Expedited - Critical Brands" if spec["name"] == "Acme Consumer Brands"
            else "Standard Enforcement SLA"
        ].id
        out[spec["name"]] = v
    db.flush()
    logger.info("Vendors: %s", ", ".join(out))
    return out


def seed_users(db, roles: dict, vendors: dict) -> dict:
    definitions = [
        # email, first, last, role, vendor, title
        ("admin@sentinel.local", "Sentinel", "Administrator",
         RoleCode.SUPER_ADMIN.value, None, "Platform Owner"),
        ("platform.admin@sentinel.local", "Priya", "Raman",
         RoleCode.ADMIN.value, None, "Platform Administrator"),
        ("admin@acme.local", "Dana", "Whitfield",
         RoleCode.ADMIN.value, "Acme Consumer Brands", "Head of Brand Protection"),
        ("user@acme.local", "Leo", "Marchetti",
         RoleCode.VENDOR_USER.value, "Acme Consumer Brands", "IP Enforcement Analyst"),
        ("analyst@acme.local", "Nina", "Osei",
         RoleCode.VENDOR_USER.value, "Acme Consumer Brands", "IP Enforcement Analyst"),
        ("user@global.local", "Marcus", "Adeyemi",
         RoleCode.VENDOR_USER.value, "Global Retail Group", "Brand Protection Lead"),
        ("admin@global.local", "Ivy", "Chen",
         RoleCode.ADMIN.value, "Global Retail Group", "Brand Protection Director"),
        ("user@txoutdoor.local", "Rosa", "Delgado",
         RoleCode.VENDOR_USER.value, "Texas Outdoor Products", "Legal Operations"),
        ("user@demobrand.local", "Sam", "Okafor",
         RoleCode.VENDOR_USER.value, "Demo Brand Corporation", "Brand Manager"),
    ]
    out = {}
    for email, first, last, role_code, vendor_name, title in definitions:
        u = db.execute(select(User).where(User.email == email)).scalars().first()
        if u is None:
            u = User(email=email)
            db.add(u)
        u.first_name, u.last_name, u.title = first, last, title
        u.password_hash = hash_password(DEMO_PASSWORD)
        u.role_id = roles[role_code].id
        u.vendor_id = vendors[vendor_name].id if vendor_name else None
        u.status = UserStatus.ACTIVE.value
        u.must_change_password = False
        out[email] = u
    db.flush()

    for email, u in out.items():
        if u.vendor_id:
            existing = db.execute(
                select(UserVendorAssignment).where(
                    UserVendorAssignment.user_id == u.id,
                    UserVendorAssignment.vendor_id == u.vendor_id,
                )
            ).scalars().first()
            if existing is None:
                db.add(UserVendorAssignment(
                    user_id=u.id, vendor_id=u.vendor_id, is_primary=True,
                ))
    db.flush()
    logger.info("Users: %s", len(out))
    return out


# --------------------------------------------------------------------------- #
def build_case(db, vendor, product_spec, marketplace, creator, assignee, created_at):
    name, sku, brand, trademark, msrp = product_spec

    product = db.execute(
        select(Product).where(Product.vendor_id == vendor.id, Product.sku == sku)
    ).scalars().first()
    if product is None:
        product = Product(
            vendor_id=vendor.id, name=name, sku=sku, brand=brand,
            trademark=trademark, msrp=msrp, currency="USD",
            product_url=f"https://{vendor.name.split()[0].lower()}.example.com/p/{sku.lower()}",
            description=f"Genuine {brand} product. Authorized channels only.",
            created_by_id=creator.id,
        )
        db.add(product)
        db.flush()

    seller, country = rng.choice(SELLERS)
    infringement = rng.choices(
        [InfringementType.COUNTERFEIT.value, InfringementType.TRADEMARK.value,
         InfringementType.COPYRIGHT.value, InfringementType.UNAUTHORIZED_SELLER.value,
         InfringementType.PRODUCT_MISUSE.value],
        weights=[42, 26, 10, 16, 6],
    )[0]
    priority = rng.choices(
        [Priority.CRITICAL.value, Priority.HIGH.value,
         Priority.MEDIUM.value, Priority.LOW.value],
        weights=[10, 28, 45, 17],
    )[0]

    policy = sla.resolve_policy(db, vendor.id)
    case = Case(
        case_number=next_case_number(db, created_at),
        vendor_id=vendor.id,
        title=f"{name} - Suspected "
              f"{infringement.replace('_', ' ').title()}",
        description=(
            f"Listing surfaced by marketplace sweep on {marketplace.name}. "
            f"Reported by {creator.full_name}."
        ),
        product_id=product.id,
        marketplace_id=marketplace.id,
        infringement_type=infringement,
        status=CaseStatus.DRAFT.value,
        priority=priority,
        autonomy_tier=vendor.autonomy_tier,
        requires_approval=True,
        sla_policy_id=policy.id if policy else None,
        sla_deadline=sla.compute_deadline(created_at, policy, priority),
        assigned_to_id=assignee.id,
        created_by_id=creator.id,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(case)
    db.flush()

    listing = Listing(
        vendor_id=vendor.id, case_id=case.id, marketplace_id=marketplace.id,
        title=f"{brand} {name.split(' ', 1)[-1]} - New Sealed - Fast Shipping",
        url=f"https://{marketplace.code.lower()}.example.com/itm/"
            f"{rng.randint(10**9, 10**10 - 1)}",
        seller_name=seller,
        seller_url=f"https://{marketplace.code.lower()}.example.com/usr/{seller}",
        seller_country=country,
        price=round(float(msrp) * rng.uniform(0.16, 0.92), 2),
        currency="USD",
        listing_date=created_at - dt.timedelta(days=rng.randint(1, 60)),
        created_at=created_at,
        captured_at=created_at,
    )
    db.add(listing)
    db.flush()
    case.listing_id = listing.id

    db.add(CaseAssignment(
        case_id=case.id, user_id=assignee.id, vendor_id=vendor.id,
        assigned_by_id=creator.id, note="Initial assignment", assigned_at=created_at,
    ))
    audit.record(
        db, action=AuditAction.CASE_CREATED.value, user=creator,
        object_type="case", object_id=case.id, object_label=case.case_number,
        vendor_id=vendor.id,
        new_value={"case_number": case.case_number, "vendor": vendor.name,
                   "product": product.name, "marketplace": marketplace.name},
    )
    db.commit()
    return case


async def seed_cases(db, vendors, users, marketplaces, count: int) -> list:
    vendor_users = {}
    for v in vendors.values():
        vendor_users[v.name] = [
            u for u in users.values() if u.vendor_id == v.id
        ] or [users["admin@sentinel.local"]]

    mp_list = list(marketplaces.values())
    now = utcnow()
    cases = []

    for i in range(count):
        vendor_list = list(vendors.values())
        vendor = vendor_list[i % len(vendor_list)]
        specs = PRODUCTS[vendor.name]
        # Step the product index by the vendor's own cycle, otherwise the two
        # rotations alias and every case for a vendor lands on one product.
        product_spec = specs[(i // len(vendor_list)) % len(specs)]
        marketplace = rng.choice(mp_list)
        pool = vendor_users[vendor.name]
        creator = rng.choice(pool)
        assignee = rng.choice(pool)
        # spread across the last 12 weeks so "Cases Over Time" has a shape
        created_at = now - dt.timedelta(
            days=rng.randint(0, 82), hours=rng.randint(0, 23)
        )
        case = build_case(db, vendor, product_spec, marketplace, creator, assignee, created_at)
        await run_pipeline(db, case, triggered_by_id=creator.id)
        db.commit()
        cases.append(case)
        if (i + 1) % 5 == 0:
            logger.info("  ... %s/%s cases analysed", i + 1, count)

    return cases


def advance_lifecycle(db, cases, users) -> None:
    """Push a realistic share of cases through approval, filing and outcome."""
    approved = rejected = filed = concluded = 0

    for case in cases:
        db.refresh(case)
        if case.status != CaseStatus.AWAITING_APPROVAL.value:
            continue
        action = enforcement_service.open_action_for_case(db, case.id)
        if action is None:
            continue

        # Who decides: the assignee, else any user in the tenant.
        decider = db.get(User, case.assigned_to_id) or next(
            (u for u in users.values() if u.vendor_id == case.vendor_id), None
        )
        if decider is None:
            continue

        roll = rng.random()
        if roll < 0.18:
            continue                                    # still awaiting a human
        if roll < 0.30:
            enforcement_service.record_decision(
                db, case=case, action=action, user=decider,
                decision=ApprovalDecision.REJECTED.value,
                reason="Seller confirmed as an authorized reseller after manual review.",
            )
            rejected += 1
            db.commit()
            continue

        enforcement_service.record_decision(
            db, case=case, action=action, user=decider,
            decision=ApprovalDecision.APPROVED.value,
            comments="Evidence reviewed. Proceeding with the recommended action.",
        )
        approved += 1
        db.commit()

        if rng.random() < 0.25:
            continue                                    # approved, not yet filed

        try:
            enforcement_service.submit_action(
                db, case=case, action=action, user=decider
            )
            filed += 1
            db.commit()
        except Exception as exc:
            logger.warning("Filing skipped for %s: %s", case.case_number, exc)
            db.rollback()
            continue

        if rng.random() < 0.72:
            enforcement_service.simulate_marketplace_response(
                db, case=case, action=action, user=decider
            )
            concluded += 1
            db.commit()

    logger.info(
        "Lifecycle: %s approved, %s rejected, %s filed, %s concluded.",
        approved, rejected, filed, concluded,
    )


def refresh_sla(db) -> None:
    changed = sla.refresh_all(db, limit=5000)
    logger.info("SLA sweep updated %s case(s).", changed)


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Sentinel IP AI demo data.")
    parser.add_argument("--reset", action="store_true", help="Delete existing data first.")
    parser.add_argument("--cases", type=int, default=28, help="Number of demo cases.")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation on --reset.")
    args = parser.parse_args()

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if args.reset:
            if not args.yes:
                answer = input("This deletes ALL data. Type 'reset' to confirm: ")
                if answer.strip().lower() != "reset":
                    logger.info("Aborted.")
                    return 1
            reset(db)
        elif db.execute(select(Case.id).limit(1)).first():
            logger.error(
                "The database already contains cases. Re-run with --reset to wipe it."
            )
            return 1

        roles = seed_roles(db)
        marketplaces = seed_marketplaces(db)
        policies = seed_sla_policies(db)
        vendors = seed_vendors(db, policies)
        users = seed_users(db, roles, vendors)
        db.commit()

        logger.info("Generating %s demo cases through the real agent pipeline...", args.cases)
        cases = asyncio.run(seed_cases(db, vendors, users, marketplaces, args.cases))
        advance_lifecycle(db, cases, users)
        refresh_sla(db)
        db.commit()

        print(f"""
================================================================================
 Sentinel IP AI - demo data ready
================================================================================
 Vendors ....... {len(vendors)}
 Users ......... {len(users)}
 Cases ......... {len(cases)}
 Marketplaces .. {len(marketplaces)}

 DEVELOPMENT SIGN-IN (all accounts share this password)
   password: {DEMO_PASSWORD}

   admin@sentinel.local ............ Super Admin       (sees every vendor)
   platform.admin@sentinel.local ... Admin, platform   (manages users/vendors)
   admin@acme.local ................ Admin, Acme only  (tenant administrator)
   user@acme.local ................. Vendor User, Acme Consumer Brands
   analyst@acme.local .............. Vendor User, Acme Consumer Brands
   admin@global.local .............. Admin, Global Retail Group only
   user@global.local ............... Vendor User, Global Retail Group
   user@txoutdoor.local ............ Vendor User, Texas Outdoor Products
   user@demobrand.local ............ Vendor User, Demo Brand Corporation

 TENANT ISOLATION CHECK
   Sign in as user@acme.local: only Acme Consumer Brands cases are visible.
   Sign in as admin@sentinel.local: every vendor's cases are visible.

 These credentials are for local development only. Change SEED_DEFAULT_PASSWORD
 and never run this script against a production database.
================================================================================
""")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
