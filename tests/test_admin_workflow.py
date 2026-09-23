"""The end-to-end administrator workflow, steps 1-16 of the specification.

One test, run in order, so a regression anywhere in the chain fails loudly.
"""
from __future__ import annotations

from tests.conftest import Client

NEW_VENDOR = "New Vendor Company"
NEW_USER_EMAIL = "workflow.user@newvendor.local"
NEW_USER_PASSWORD = "Workfl0w!Password"


def test_admin_workflow_end_to_end(seeded):
    # ---- Step 1: Super Admin signs in ----------------------------------
    superadmin = Client(seeded["superadmin"])
    me = superadmin.get("/api/auth/me").json()
    assert me["role_code"] == "SUPER_ADMIN"
    assert me["vendor_id"] is None

    # ---- Steps 2-4: create a vendor ------------------------------------
    created = superadmin.post("/api/vendors", json={
        "name": NEW_VENDOR,
        "legal_name": "New Vendor Company LLC",
        "contact_name": "Jordan Reyes",
        "contact_email": "ip@newvendor.local",
        "country": "United States",
        "industry": "Consumer Goods",
        "autonomy_tier": 1,
        "status": "ACTIVE",
    })
    assert created.status_code == 201, created.text
    vendor = created.json()
    assert vendor["autonomy_tier"] == 1, "new vendors must default to Tier 1"

    # ---- Step 5-6: the vendor dropdown is fed from the database ---------
    options = superadmin.get("/api/vendors/options").json()
    assert NEW_VENDOR in [o["name"] for o in options]

    # ---- Step 7-8: create a Vendor User bound to that vendor -----------
    user_res = superadmin.post("/api/users", json={
        "first_name": "Workflow", "last_name": "User",
        "email": NEW_USER_EMAIL, "role_code": "VENDOR_USER",
        "vendor_id": vendor["id"], "password": NEW_USER_PASSWORD,
        "must_change_password": False,
    })
    assert user_res.status_code == 201, user_res.text
    assert user_res.json()["user"]["vendor_name"] == NEW_VENDOR

    # ---- Step 9: the user signs in --------------------------------------
    vendor_user = Client()
    vendor_user.login(NEW_USER_EMAIL, NEW_USER_PASSWORD)
    profile = vendor_user.get("/api/auth/me").json()
    assert profile["vendor_name"] == NEW_VENDOR
    assert profile["is_global"] is False

    # ---- Step 10: the user sees only their vendor's cases ---------------
    listing = vendor_user.get("/api/cases?page_size=200").json()
    assert listing["total"] == 0, "a brand-new vendor starts with no cases"

    # A case exists elsewhere on the platform...
    other = Client(seeded["user_a"])
    other_case = other.post("/api/cases", json={
        "product_name": "Other tenant product",
        "marketplace_id": seeded["marketplace_id"],
        "listing_url": "https://example.com/itm/other-tenant",
        "infringement_type": "COUNTERFEIT", "run_analysis": False,
    }).json()
    # ...and is invisible to the new user, by list and by direct id.
    assert vendor_user.get("/api/cases?page_size=200").json()["total"] == 0
    assert vendor_user.get(f"/api/cases/{other_case['id']}").status_code in (403, 404)

    # ---- Step 11: the user creates a case -------------------------------
    case_res = vendor_user.post("/api/cases", json={
        "product_name": "NVC Signature Backpack",
        "product_sku": "NVC-SB-001",
        "brand": "NVC", "trademark": "NVC SIGNATURE", "msrp": 189.0,
        "marketplace_id": seeded["marketplace_id"],
        "listing_url": "https://example.com/itm/nvc-counterfeit",
        "seller_name": "GreyMarket_Direct", "listing_price": 38.0,
        "infringement_type": "COUNTERFEIT", "priority": "HIGH",
        "run_analysis": False,
    })
    assert case_res.status_code == 201, case_res.text
    case = case_res.json()
    assert case["vendor_id"] == vendor["id"]
    assert case["case_number"].startswith("CASE-")

    # ---- Step 12: the AI pipeline runs ----------------------------------
    assert vendor_user.post(f"/api/cases/{case['id']}/analyze").status_code == 200
    detail = vendor_user.get(f"/api/cases/{case['id']}").json()
    assert [a["status"] for a in detail["agents"][:3]] == ["COMPLETE"] * 3
    assert detail["agents"][3]["status"] == "AWAITING_APPROVAL"

    # ---- Step 13: the user reviews the recommendation -------------------
    assert detail["status"] == "AWAITING_APPROVAL"
    assert detail["ai_confidence"] is not None
    assert detail["enforcement"]["action_label"]
    assert detail["enforcement"]["reasoning"]
    assert detail["enforcement"]["has_valid_approval"] is False
    assert detail["evidence"], "the agents must have collected evidence"

    # Nothing can be filed at this point.
    blocked = superadmin.post(f"/api/cases/{case['id']}/file")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "HUMAN_APPROVAL_REQUIRED"

    # ---- Step 14: the user approves -------------------------------------
    approved = vendor_user.post(f"/api/cases/{case['id']}/approve",
                                json={"comments": "Evidence reviewed and sufficient."})
    assert approved.status_code == 200, approved.text
    approval = approved.json()["approval"]
    assert approval["decided_by_email"] == NEW_USER_EMAIL
    assert approval["ai_recommendation"]
    assert approval["evidence_item_count"] >= 1

    # ---- Step 15: Filing & Tracking updates the enforcement status ------
    after = vendor_user.get(f"/api/cases/{case['id']}").json()
    assert after["status"] == "SUBMITTED"
    assert after["enforcement"]["submitted_at"] is not None
    assert after["enforcement"]["is_simulated"] is True

    tracked = superadmin.post(
        f"/api/cases/{case['id']}/enforcement/response?result=LISTING_REMOVED"
    )
    assert tracked.status_code == 200
    final = vendor_user.get(f"/api/cases/{case['id']}").json()
    assert final["status"] == "RESOLVED"

    # ---- Step 16: every action is in the audit trail --------------------
    trail = superadmin.get(
        f"/api/audit?vendor_id={vendor['id']}&page_size=200"
    ).json()["items"]
    actions = {r["action"] for r in trail}
    for expected in {
        "CASE_CREATED", "AI_AGENT_EXECUTED", "AI_RECOMMENDATION_GENERATED",
        "APPROVAL_GRANTED", "ENFORCEMENT_FILED", "STATUS_CHANGED",
    }:
        assert expected in actions, f"missing {expected} in the audit trail"

    platform_trail = superadmin.get("/api/audit?page_size=200").json()["items"]
    platform_actions = {r["action"] for r in platform_trail}
    assert "VENDOR_CREATED" in platform_actions
    assert "USER_CREATED" in platform_actions
    assert "VENDOR_ASSIGNMENT_CHANGED" in platform_actions

    # The vendor user's own view of the trail stays inside their tenant.
    own_trail = vendor_user.get("/api/audit?page_size=200").json()["items"]
    assert all(r["vendor_id"] in (None, vendor["id"]) for r in own_trail)
