"""The ten mandated authorization tests, plus the neighbouring cases.

Each test here maps to a numbered requirement in the specification. They are
the regression net around the two security boundaries: tenant isolation and
the human approval gate.
"""
from __future__ import annotations

import pytest

from tests.conftest import TEST_PASSWORD, Client


# --------------------------------------------------------------------------- #
# Test 1 - Unauthenticated user cannot access /cases
# --------------------------------------------------------------------------- #
def test_1_unauthenticated_cannot_access_cases(anon):
    res = anon.get("/api/cases")
    assert res.status_code == 401
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_AUTHENTICATED"


def test_1b_unauthenticated_page_redirects_to_login(anon):
    res = anon.get("/cases", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"].startswith("/login")


@pytest.mark.parametrize("path", [
    "/api/cases", "/api/users", "/api/vendors", "/api/dashboard",
    "/api/audit", "/api/agents", "/api/evidence", "/api/enforcement",
    "/api/notifications", "/api/admin/sla-policies", "/api/meta",
])
def test_1c_every_business_endpoint_requires_auth(anon, path):
    assert anon.get(path).status_code == 401


def test_1d_invalid_token_is_rejected(anon):
    anon.headers["Authorization"] = "Bearer not.a.real.token"
    res = anon.get("/api/cases")
    assert res.status_code == 401


# --------------------------------------------------------------------------- #
# Test 2 - Vendor User A cannot access Vendor B's case
# --------------------------------------------------------------------------- #
def test_2_vendor_user_cannot_read_other_vendors_case(vendor_a, vendor_b, make_case):
    case_b = make_case(vendor_b)
    res = vendor_a.get(f"/api/cases/{case_b['id']}")
    assert res.status_code in (403, 404)
    assert res.json()["error"]["code"] in ("VENDOR_ACCESS_DENIED", "NOT_FOUND")
    # And nothing about the other tenant leaks in the message.
    assert "Vendor B" not in res.text


def test_2b_case_list_is_scoped_to_own_vendor(vendor_a, vendor_b, make_case, seeded):
    make_case(vendor_a)
    make_case(vendor_b)
    rows = vendor_a.get("/api/cases?page_size=200").json()["items"]
    assert rows, "vendor A should see its own cases"
    assert {r["vendor_id"] for r in rows} == {seeded["vendor_a_id"]}


def test_2c_vendor_filter_cannot_be_forced_to_another_tenant(vendor_a, seeded):
    res = vendor_a.get(f"/api/cases?vendor_id={seeded['vendor_b_id']}")
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "VENDOR_ACCESS_DENIED"


def test_2d_evidence_of_another_vendor_is_not_reachable(vendor_a, vendor_b, make_case):
    case_b = make_case(vendor_b, run_analysis=True)
    evidence = vendor_b.get(f"/api/cases/{case_b['id']}/evidence").json()
    assert evidence, "the pipeline should have produced evidence"
    res = vendor_a.get(f"/api/evidence/{evidence[0]['id']}/download")
    assert res.status_code in (403, 404)


# --------------------------------------------------------------------------- #
# Test 3 - Vendor User A cannot modify Vendor B's case
# --------------------------------------------------------------------------- #
def test_3_vendor_user_cannot_modify_other_vendors_case(vendor_a, vendor_b, make_case):
    case_b = make_case(vendor_b)
    res = vendor_a.put(f"/api/cases/{case_b['id']}", json={"title": "hijacked"})
    assert res.status_code in (403, 404)

    after = vendor_b.get(f"/api/cases/{case_b['id']}").json()
    assert after["title"] != "hijacked"


def test_3b_vendor_user_cannot_approve_other_vendors_case(vendor_a, vendor_b, make_case):
    case_b = make_case(vendor_b, run_analysis=True)
    res = vendor_a.post(f"/api/cases/{case_b['id']}/approve", json={"comments": "ok"})
    assert res.status_code in (403, 404)


def test_3c_vendor_user_cannot_upload_evidence_to_another_vendors_case(
    vendor_a, vendor_b, make_case
):
    case_b = make_case(vendor_b)
    res = vendor_a.post(
        f"/api/cases/{case_b['id']}/evidence/url",
        json={"category": "EXTERNAL_REFERENCE", "title": "Cross-tenant probe",
              "url": "https://example.com/x"},
    )
    assert res.status_code in (403, 404)


def test_3d_vendor_user_cannot_create_a_case_for_another_vendor(vendor_a, seeded):
    res = vendor_a.post("/api/cases", json={
        "product_name": "Cross tenant", "marketplace_id": seeded["marketplace_id"],
        "listing_url": "https://example.com/itm/xt", "infringement_type": "COUNTERFEIT",
        "vendor_id": seeded["vendor_b_id"], "run_analysis": False,
    })
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "VENDOR_ACCESS_DENIED"


# --------------------------------------------------------------------------- #
# Test 4 - Vendor User cannot create another Super Admin
# --------------------------------------------------------------------------- #
def test_4_vendor_user_cannot_create_a_super_admin(vendor_a):
    res = vendor_a.post("/api/users", json={
        "first_name": "Mallory", "last_name": "Escalation",
        "email": "mallory@test.local", "role_code": "SUPER_ADMIN",
        "password": "Sup3rStrong!Pass",
    })
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "PERMISSION_DENIED"


def test_4b_platform_admin_cannot_mint_a_super_admin(admin):
    res = admin.post("/api/users", json={
        "first_name": "Mallory", "last_name": "Escalation",
        "email": "mallory2@test.local", "role_code": "SUPER_ADMIN",
        "password": "Sup3rStrong!Pass",
    })
    assert res.status_code == 403
    assert "Super Admin" in res.json()["error"]["message"]


# --------------------------------------------------------------------------- #
# Test 5 - Vendor User cannot access /api/users
# --------------------------------------------------------------------------- #
def test_5_vendor_user_cannot_list_users(vendor_a):
    res = vendor_a.get("/api/users")
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "PERMISSION_DENIED"


def test_5b_vendor_user_cannot_list_vendors(vendor_a):
    assert vendor_a.get("/api/vendors").status_code == 403


def test_5c_vendor_user_cannot_reach_admin_configuration(vendor_a, seeded):
    assert vendor_a.post("/api/admin/sla-policies", json={
        "name": "Sneaky", "response_hours": 1, "resolution_hours": 1,
        "priority": "LOW", "at_risk_percent": 50,
    }).status_code == 403
    assert vendor_a.put(
        f"/api/admin/autonomy/vendors/{seeded['vendor_a_id']}?tier=3"
    ).status_code == 403


def test_5d_vendor_user_page_route_is_redirected(vendor_a):
    res = vendor_a.get("/admin/users", follow_redirects=False)
    assert res.status_code == 302
    assert "/dashboard" in res.headers["location"]


# --------------------------------------------------------------------------- #
# Test 6 - Super Admin can see all vendors
# --------------------------------------------------------------------------- #
def test_6_super_admin_sees_all_vendors(superadmin, seeded):
    page = superadmin.get("/api/vendors?page_size=200").json()
    ids = {v["id"] for v in page["items"]}
    assert seeded["vendor_a_id"] in ids
    assert seeded["vendor_b_id"] in ids


# --------------------------------------------------------------------------- #
# Test 7 - Super Admin can see all cases
# --------------------------------------------------------------------------- #
def test_7_super_admin_sees_all_cases(superadmin, vendor_a, vendor_b, make_case, seeded):
    make_case(vendor_a)
    make_case(vendor_b)
    rows = superadmin.get("/api/cases?page_size=200").json()["items"]
    vendors = {r["vendor_id"] for r in rows}
    assert seeded["vendor_a_id"] in vendors
    assert seeded["vendor_b_id"] in vendors


def test_7b_super_admin_dashboard_is_platform_scoped(superadmin):
    data = superadmin.get("/api/dashboard").json()
    assert data["scope"] == "platform"


def test_7c_vendor_dashboard_is_tenant_scoped(vendor_a):
    data = vendor_a.get("/api/dashboard").json()
    assert data["scope"] == "vendor"
    assert data["vendor_name"] == "Vendor A"


# --------------------------------------------------------------------------- #
# Test 8 - Creating a Vendor User requires a vendor
# --------------------------------------------------------------------------- #
def test_8_vendor_user_requires_a_vendor(superadmin):
    res = superadmin.post("/api/users", json={
        "first_name": "No", "last_name": "Vendor",
        "email": "novendor@test.local", "role_code": "VENDOR_USER",
        "password": "Str0ng!Password9",
    })
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "vendor" in res.text.lower()


def test_8b_vendor_user_with_a_vendor_is_created(superadmin, seeded):
    res = superadmin.post("/api/users", json={
        "first_name": "With", "last_name": "Vendor",
        "email": "withvendor@test.local", "role_code": "VENDOR_USER",
        "vendor_id": seeded["vendor_a_id"], "password": "Str0ng!Password9",
    })
    assert res.status_code == 201, res.text
    assert res.json()["user"]["vendor_id"] == seeded["vendor_a_id"]


def test_8c_vendor_user_cannot_be_pointed_at_a_missing_vendor(superadmin):
    res = superadmin.post("/api/users", json={
        "first_name": "Ghost", "last_name": "Vendor",
        "email": "ghost@test.local", "role_code": "VENDOR_USER",
        "vendor_id": 999999, "password": "Str0ng!Password9",
    })
    assert res.status_code == 422


# --------------------------------------------------------------------------- #
# Test 9 - Creating a Super Admin does not require a vendor
# --------------------------------------------------------------------------- #
def test_9_super_admin_needs_no_vendor(superadmin):
    res = superadmin.post("/api/users", json={
        "first_name": "Second", "last_name": "Super",
        "email": "second.super@test.local", "role_code": "SUPER_ADMIN",
        "password": "Str0ng!Password9",
    })
    assert res.status_code == 201, res.text
    assert res.json()["user"]["vendor_id"] is None


def test_9b_super_admin_must_not_be_tied_to_a_vendor(superadmin, seeded):
    res = superadmin.post("/api/users", json={
        "first_name": "Bad", "last_name": "Super",
        "email": "bad.super@test.local", "role_code": "SUPER_ADMIN",
        "vendor_id": seeded["vendor_a_id"], "password": "Str0ng!Password9",
    })
    assert res.status_code == 422


# --------------------------------------------------------------------------- #
# Test 10 - Enforcement cannot be submitted without human approval
# --------------------------------------------------------------------------- #
def test_10_filing_without_approval_is_refused(superadmin, vendor_a, make_case, seeded):
    case = make_case(vendor_a, run_analysis=True)
    detail = vendor_a.get(f"/api/cases/{case['id']}").json()
    assert detail["status"] == "AWAITING_APPROVAL"
    assert detail["enforcement"] is not None
    assert detail["enforcement"]["has_valid_approval"] is False

    # A Super Admin holds enforcement:submit and still cannot file.
    res = superadmin.post(f"/api/cases/{case['id']}/file")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "HUMAN_APPROVAL_REQUIRED"

    after = vendor_a.get(f"/api/cases/{case['id']}").json()
    assert after["enforcement"]["submitted_at"] is None
    assert after["enforcement"]["status"] == "AWAITING_APPROVAL"


def test_10b_approval_then_filing_succeeds(vendor_a, make_case):
    case = make_case(vendor_a, run_analysis=True)
    res = vendor_a.post(f"/api/cases/{case['id']}/approve",
                        json={"comments": "Evidence reviewed."})
    assert res.status_code == 200, res.text

    detail = vendor_a.get(f"/api/cases/{case['id']}").json()
    assert detail["enforcement"]["has_valid_approval"] is True
    assert detail["enforcement"]["submitted_at"] is not None
    assert detail["enforcement"]["is_simulated"] is True
    assert detail["status"] == "SUBMITTED"


def test_10c_rejection_requires_a_reason(vendor_a, make_case):
    case = make_case(vendor_a, run_analysis=True)
    res = vendor_a.post(f"/api/cases/{case['id']}/reject", json={})
    assert res.status_code == 422
    assert "reason" in res.text.lower()


def test_10d_request_changes_requires_comments(vendor_a, make_case):
    case = make_case(vendor_a, run_analysis=True)
    res = vendor_a.post(f"/api/cases/{case['id']}/request-changes", json={})
    assert res.status_code == 422


def test_10e_a_new_recommendation_invalidates_the_old_approval(vendor_a, make_case):
    """An approval authorizes one specific recommendation version, not the case."""
    from app.database import SessionLocal
    from app.models import Approval, Case, EnforcementAction
    from sqlalchemy import select

    case = make_case(vendor_a, run_analysis=True)
    vendor_a.post(f"/api/cases/{case['id']}/request-changes",
                  json={"comments": "Collect a test purchase first."})
    # Re-run analysis: the strategist supersedes the old action with a new version.
    vendor_a.post(f"/api/cases/{case['id']}/analyze")

    db = SessionLocal()
    try:
        actions = db.execute(
            select(EnforcementAction)
            .where(EnforcementAction.case_id == case["id"])
            .order_by(EnforcementAction.id.desc())
        ).scalars().all()
        assert len(actions) >= 2
        latest = actions[0]
        approvals = db.execute(
            select(Approval).where(
                Approval.enforcement_action_id == latest.id,
                Approval.decision == "APPROVED",
            )
        ).scalars().all()
        assert approvals == [], "the new recommendation must not inherit an approval"
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Tenant-scoped Admin (an Admin attached to one vendor)
# --------------------------------------------------------------------------- #
def test_tenant_scoped_admin_sees_only_its_own_vendor(superadmin, seeded):
    created = superadmin.post("/api/users", json={
        "first_name": "Tenant", "last_name": "Admin",
        "email": "tenant.admin@test.local", "role_code": "ADMIN",
        "vendor_id": seeded["vendor_a_id"], "password": "Str0ng!Password9",
    })
    assert created.status_code == 201, created.text

    client = Client()
    client.login("tenant.admin@test.local", "Str0ng!Password9")

    vendors = client.get("/api/vendors?page_size=200").json()["items"]
    assert {v["id"] for v in vendors} == {seeded["vendor_a_id"]}

    users = client.get("/api/users?page_size=200").json()["items"]
    assert all(u["vendor_id"] == seeded["vendor_a_id"] for u in users)

    # ...and cannot create a vendor at all.
    assert client.post("/api/vendors", json={"name": "Sneaky Corp"}).status_code == 403
