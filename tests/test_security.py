"""Authentication, session handling, SLA, audit trail and input validation."""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.auth.security import (
    hash_password,
    validate_password_strength,
    verify_password,
)
from app.errors import ValidationFailed
from app.main import app
from app.services import sla
from tests.conftest import TEST_PASSWORD, Client


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def test_password_hash_is_salted_and_verifiable():
    a = hash_password("Str0ng!Password9")
    b = hash_password("Str0ng!Password9")
    assert a != b                                  # per-hash salt
    assert not a.startswith("Str0ng")              # never plaintext
    assert verify_password("Str0ng!Password9", a)
    assert not verify_password("wrong", a)


@pytest.mark.parametrize("weak", [
    "short1!A", "alllowercase1!", "ALLUPPERCASE1!", "NoDigitsHere!!", "NoSymbols123AB",
    "password",
])
def test_weak_passwords_are_rejected(weak):
    with pytest.raises(ValidationFailed):
        validate_password_strength(weak)


def test_strong_password_is_accepted():
    validate_password_strength("Str0ng!Password9")


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def test_login_sets_an_httponly_session_cookie(seeded):
    client = TestClient(app)
    res = client.post("/api/auth/login",
                      json={"email": seeded["user_a"], "password": TEST_PASSWORD})
    assert res.status_code == 200
    raw = res.headers.get("set-cookie", "")
    assert "sentinel_session" in raw
    assert "HttpOnly" in raw
    assert "SameSite=lax" in raw or "samesite=lax" in raw.lower()


def test_login_response_never_leaks_the_password_hash(seeded):
    client = TestClient(app)
    res = client.post("/api/auth/login",
                      json={"email": seeded["user_a"], "password": TEST_PASSWORD})
    body = res.text
    assert "password_hash" not in body
    assert "$2b$" not in body


def test_wrong_password_is_rejected_without_saying_which_field(seeded):
    client = TestClient(app)
    res = client.post("/api/auth/login",
                      json={"email": seeded["user_a"], "password": "definitely-wrong"})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "INVALID_CREDENTIALS"
    # The same message for an unknown account - no user enumeration.
    other = client.post("/api/auth/login",
                        json={"email": "nobody@test.local", "password": "x"})
    assert other.json()["error"]["message"] == res.json()["error"]["message"]


def test_logout_invalidates_the_token(seeded):
    client = Client(seeded["user_a"])
    assert client.get("/api/cases").status_code == 200
    assert client.post("/api/auth/logout", json={}).status_code == 200
    assert client.get("/api/cases").status_code == 401


def test_csrf_is_required_for_cookie_authenticated_writes(seeded):
    client = TestClient(app)
    res = client.post("/api/auth/login",
                      json={"email": seeded["user_a"], "password": TEST_PASSWORD})
    assert res.status_code == 200
    # No X-CSRF-Token header: the write must be refused.
    write = client.post("/api/cases", json={
        "product_name": "CSRF probe", "marketplace_id": seeded["marketplace_id"],
        "listing_url": "https://example.com/itm/csrf", "infringement_type": "OTHER",
        "run_analysis": False,
    })
    assert write.status_code == 403
    assert write.json()["error"]["code"] == "CSRF_FAILED"


def test_bearer_token_is_exempt_from_csrf(seeded):
    client = TestClient(app)
    res = client.post("/api/auth/login",
                      json={"email": seeded["user_a"], "password": TEST_PASSWORD})
    token = res.json()["access_token"]
    client.cookies.clear()
    write = client.post(
        "/api/cases",
        headers={"Authorization": f"Bearer {token}"},
        json={"product_name": "Bearer probe",
              "marketplace_id": seeded["marketplace_id"],
              "listing_url": "https://example.com/itm/bearer",
              "infringement_type": "OTHER", "run_analysis": False},
    )
    assert write.status_code == 201, write.text


def test_disabled_account_cannot_sign_in(superadmin, seeded):
    created = superadmin.post("/api/users", json={
        "first_name": "Soon", "last_name": "Disabled",
        "email": "disabled@test.local", "role_code": "VENDOR_USER",
        "vendor_id": seeded["vendor_a_id"], "password": "Str0ng!Password9",
    })
    user_id = created.json()["user"]["id"]
    superadmin.put(f"/api/users/{user_id}", json={"status": "DISABLED"})

    client = TestClient(app)
    res = client.post("/api/auth/login",
                      json={"email": "disabled@test.local", "password": "Str0ng!Password9"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ACCOUNT_DISABLED"


# --------------------------------------------------------------------------- #
# Response headers
# --------------------------------------------------------------------------- #
def test_security_headers_are_present(anon):
    res = anon.get("/api/health")
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in res.headers
    assert "script-src 'self'" in res.headers["Content-Security-Policy"]
    assert res.headers["Cache-Control"] == "no-store"
    assert res.headers.get("X-Correlation-ID")


def test_errors_do_not_leak_stack_traces(vendor_a):
    res = vendor_a.get("/api/cases/99999999")
    assert res.status_code in (403, 404)
    assert "Traceback" not in res.text
    assert "sqlalchemy" not in res.text.lower()


# --------------------------------------------------------------------------- #
# Input validation / injection
# --------------------------------------------------------------------------- #
def test_sql_injection_in_search_is_parameterised(vendor_a, make_case):
    make_case(vendor_a)
    res = vendor_a.get("/api/cases", params={"q": "'; DROP TABLE cases; --"})
    assert res.status_code == 200
    # The table is still there.
    assert vendor_a.get("/api/cases").status_code == 200


def test_xss_payload_is_stored_verbatim_and_escaped_by_the_client(vendor_a, make_case):
    """The API stores text as given; the UI escapes it. What must not happen is
    the API rendering it into HTML itself."""
    payload = "<script>alert('xss')</script>"
    case = make_case(vendor_a, product_name=payload)
    detail = vendor_a.get(f"/api/cases/{case['id']}").json()
    assert payload in detail["title"]
    assert detail["title"].count("<script>") == 1     # stored, not executed
    # The JSON response is not HTML - no content type confusion.
    res = vendor_a.get(f"/api/cases/{case['id']}")
    assert res.headers["content-type"].startswith("application/json")


def test_invalid_infringement_type_is_rejected(vendor_a, seeded):
    res = vendor_a.post("/api/cases", json={
        "product_name": "Bad type", "marketplace_id": seeded["marketplace_id"],
        "listing_url": "https://example.com/itm/bad", "infringement_type": "NONSENSE",
        "run_analysis": False,
    })
    assert res.status_code == 422


def test_listing_url_must_be_http(vendor_a, seeded):
    res = vendor_a.post("/api/cases", json={
        "product_name": "Bad url", "marketplace_id": seeded["marketplace_id"],
        "listing_url": "javascript:alert(1)", "infringement_type": "OTHER",
        "run_analysis": False,
    })
    assert res.status_code == 422


def test_protected_status_transitions_are_not_writable(vendor_a, make_case):
    case = make_case(vendor_a)
    res = vendor_a.put(f"/api/cases/{case['id']}", json={"status": "APPROVED"})
    assert res.status_code == 409
    assert "approval and filing workflow" in res.json()["error"]["message"]


# --------------------------------------------------------------------------- #
# SLA
# --------------------------------------------------------------------------- #
def test_sla_states_across_the_window():
    start = dt.datetime(2026, 1, 1, 0, 0)
    deadline = start + dt.timedelta(hours=48)

    on_track = sla.evaluate(started_at=start, deadline=deadline,
                            now=start + dt.timedelta(hours=1))
    assert on_track["state"] == "ON_TRACK"

    at_risk = sla.evaluate(started_at=start, deadline=deadline,
                           now=start + dt.timedelta(hours=40))
    assert at_risk["state"] == "AT_RISK"

    breached = sla.evaluate(started_at=start, deadline=deadline,
                            now=start + dt.timedelta(hours=60))
    assert breached["state"] == "BREACHED"
    assert breached["is_breached"] is True
    assert "overdue" in breached["remaining_label"]

    met = sla.evaluate(started_at=start, deadline=deadline,
                       resolved_at=start + dt.timedelta(hours=10))
    assert met["state"] == "MET"


def test_priority_tightens_the_sla_window():
    from app.models import SlaPolicy
    start = dt.datetime(2026, 1, 1)
    policy = SlaPolicy(name="p", response_hours=48, resolution_hours=168,
                       at_risk_percent=75)
    critical = sla.compute_deadline(start, policy, "CRITICAL")
    medium = sla.compute_deadline(start, policy, "MEDIUM")
    low = sla.compute_deadline(start, policy, "LOW")
    assert critical < medium < low
    assert (critical - start).total_seconds() == 12 * 3600


def test_case_carries_an_sla_deadline(vendor_a, make_case):
    case = make_case(vendor_a)
    detail = vendor_a.get(f"/api/cases/{case['id']}").json()
    assert detail["sla"]["deadline"]
    assert detail["sla"]["state"] in ("ON_TRACK", "AT_RISK", "BREACHED")


def test_human_decision_stops_the_sla_clock(vendor_a, make_case):
    case = make_case(vendor_a, run_analysis=True)
    vendor_a.post(f"/api/cases/{case['id']}/approve", json={"comments": "ok"})
    detail = vendor_a.get(f"/api/cases/{case['id']}").json()
    assert detail["sla"]["state"] in ("MET", "BREACHED")


# --------------------------------------------------------------------------- #
# Audit trail
# --------------------------------------------------------------------------- #
def test_audit_records_the_whole_workflow(superadmin, vendor_a, make_case):
    case = make_case(vendor_a, run_analysis=True)
    vendor_a.get(f"/api/cases/{case['id']}")
    vendor_a.post(f"/api/cases/{case['id']}/approve", json={"comments": "Reviewed."})

    rows = superadmin.get("/api/audit?page_size=200").json()["items"]
    actions = {r["action"] for r in rows}
    for expected in {
        "LOGIN", "CASE_CREATED", "CASE_VIEWED", "AI_AGENT_EXECUTED",
        "AI_RECOMMENDATION_GENERATED", "APPROVAL_GRANTED", "ENFORCEMENT_FILED",
    }:
        assert expected in actions, f"missing audit action {expected}"


def test_audit_has_no_write_endpoints(superadmin):
    """Append-only means the API offers no way to change history."""
    assert superadmin.post("/api/audit", json={"action": "FORGED"}).status_code in (404, 405)
    assert superadmin.put("/api/audit/1", json={}).status_code in (404, 405)
    assert superadmin.delete("/api/audit/1").status_code in (404, 405)


def test_audit_is_tenant_scoped(vendor_a, vendor_b, make_case, seeded):
    make_case(vendor_b)
    rows = vendor_a.get("/api/audit?page_size=200").json()["items"]
    assert all(r["vendor_id"] in (None, seeded["vendor_a_id"]) for r in rows)


def test_audit_never_stores_a_password(superadmin, seeded):
    superadmin.post("/api/users", json={
        "first_name": "Audit", "last_name": "Probe",
        "email": "audit.probe@test.local", "role_code": "VENDOR_USER",
        "vendor_id": seeded["vendor_a_id"], "password": "Str0ng!Password9",
    })
    rows = superadmin.get("/api/audit?action=USER_CREATED&page_size=50").json()["items"]
    blob = str(rows)
    assert "Str0ng!Password9" not in blob
    assert "[REDACTED]" in blob


def test_denied_cross_tenant_access_is_audited(superadmin, vendor_a, vendor_b, make_case):
    case_b = make_case(vendor_b)
    vendor_a.get(f"/api/cases/{case_b['id']}")
    rows = superadmin.get("/api/audit?action=ACCESS_DENIED&page_size=50").json()["items"]
    assert any(r["success"] is False for r in rows)
