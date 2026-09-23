"""Failure-mode behaviour.

Written after a case page sat on loading skeletons indefinitely in the field.
The root causes were all "a single failed or slow request breaks the whole
screen, silently". These tests pin the fixes.
"""
from __future__ import annotations

import os
import re

import pytest
from sqlalchemy import select

from app.config import BASE_DIR
from app.constants import AgentRunStatus, CaseStatus
from app.database import SessionLocal
from app.models import AiAgentRun, Case
from app.services.recovery import recover_orphaned_analyses

JS_DIR = os.path.join(BASE_DIR, "frontend", "static", "js")


def _js(name: str) -> str:
    with open(os.path.join(JS_DIR, name), encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# Client contract
# --------------------------------------------------------------------------- #
def test_api_client_has_a_request_timeout():
    """Without a timeout a hung proxy leaves the UI spinning forever."""
    src = _js("api.js")
    assert "AbortController" in src
    assert "REQUEST_TIMEOUT" in src
    assert re.search(r"DEFAULT_TIMEOUT_MS\s*=\s*\d+", src)


#: `try { me = await Api.me(); } catch (e) { return; }` - a failed identity
#: lookup aborting the whole page render, with nothing shown to the user.
#: (A bare `return` inside a JSON.parse guard is fine and must not match.)
SILENT_ABORT = re.compile(r"Api\.me\(\)\s*;?\s*\}?\s*catch\s*\([^)]*\)\s*\{\s*return\s*;?\s*\}")


@pytest.mark.parametrize("name", [
    "case_detail.js", "admin_users.js", "admin_vendors.js", "admin_autonomy.js",
    "cases.js", "case_new.js", "settings.js",
])
def test_identity_lookup_never_aborts_the_page(name):
    src = _js(name)
    assert not SILENT_ABORT.search(src), (
        f"{name} aborts its bootstrap when /api/auth/me fails; the page would "
        "render as empty skeletons with no message"
    )


def test_case_page_surfaces_a_load_failure():
    src = _js("case_detail.js")
    assert "renderLoadFailure" in src
    assert "retryLoad" in src, "a failed load must offer a retry"


def test_case_page_polling_cannot_run_forever():
    """`load()` used to restart polling on every ANALYZING response, so a
    stuck case polled every 2s indefinitely."""
    src = _js("case_detail.js")
    assert "pollExhausted" in src
    assert re.search(r"POLL_LIMIT\s*=\s*\d+", src)
    assert "renderStalled" in src


def test_background_polls_are_marked_as_polls():
    assert "poll: !!silent" in _js("case_detail.js")


# --------------------------------------------------------------------------- #
# Poll requests must not write audit records
# --------------------------------------------------------------------------- #
def test_normal_view_writes_one_audit_record(superadmin, vendor_a, make_case):
    case = make_case(vendor_a)
    before = superadmin.get(
        f"/api/audit?action=CASE_VIEWED&object_id={case['id']}&page_size=100"
    ).json()["total"]

    vendor_a.get(f"/api/cases/{case['id']}")

    after = superadmin.get(
        f"/api/audit?action=CASE_VIEWED&object_id={case['id']}&page_size=100"
    ).json()["total"]
    assert after == before + 1


def test_polling_does_not_flood_the_audit_trail(superadmin, vendor_a, make_case):
    case = make_case(vendor_a)
    before = superadmin.get(
        f"/api/audit?action=CASE_VIEWED&object_id={case['id']}&page_size=100"
    ).json()["total"]

    for _ in range(10):
        res = vendor_a.get(f"/api/cases/{case['id']}?poll=true")
        assert res.status_code == 200

    after = superadmin.get(
        f"/api/audit?action=CASE_VIEWED&object_id={case['id']}&page_size=100"
    ).json()["total"]
    assert after == before, "background polls must not be audited as views"


def test_polling_still_enforces_tenant_isolation(vendor_a, vendor_b, make_case):
    """The poll shortcut must not become an authorization shortcut."""
    case_b = make_case(vendor_b)
    res = vendor_a.get(f"/api/cases/{case_b['id']}?poll=true")
    assert res.status_code in (403, 404)


def test_polling_requires_authentication(anon, vendor_a, make_case):
    case = make_case(vendor_a)
    assert anon.get(f"/api/cases/{case['id']}?poll=true").status_code == 401


# --------------------------------------------------------------------------- #
# Recovery of interrupted analyses
# --------------------------------------------------------------------------- #
def test_case_stranded_in_analyzing_is_recovered(vendor_a, make_case):
    """A restart mid-pipeline used to leave a case analysing forever."""
    case = make_case(vendor_a)
    db = SessionLocal()
    try:
        row = db.get(Case, case["id"])
        row.status = CaseStatus.ANALYZING.value
        db.add(row)
        db.add(AiAgentRun(
            case_id=row.id, vendor_id=row.vendor_id, agent_name="SCOUT",
            sequence=1, status=AgentRunStatus.RUNNING.value,
        ))
        db.commit()

        recovered = recover_orphaned_analyses(db)
        assert recovered >= 1

        db.expire_all()
        row = db.get(Case, case["id"])
        assert row.status == CaseStatus.DRAFT.value

        runs = db.execute(
            select(AiAgentRun).where(AiAgentRun.case_id == row.id)
        ).scalars().all()
        assert all(r.status != AgentRunStatus.RUNNING.value for r in runs)
        assert any("Interrupted" in (r.error or "") for r in runs)
    finally:
        db.close()


def test_recovery_is_a_no_op_when_nothing_is_stranded():
    db = SessionLocal()
    try:
        assert recover_orphaned_analyses(db) == 0
    finally:
        db.close()


def test_recovered_case_can_be_re_analysed(vendor_a, make_case):
    case = make_case(vendor_a)
    db = SessionLocal()
    try:
        row = db.get(Case, case["id"])
        row.status = CaseStatus.ANALYZING.value
        db.add(row)
        db.commit()
        recover_orphaned_analyses(db)
    finally:
        db.close()

    # DRAFT is an actionable state, so analysis is offered again.
    res = vendor_a.post(f"/api/cases/{case['id']}/analyze")
    assert res.status_code == 200

    detail = vendor_a.get(f"/api/cases/{case['id']}").json()
    assert detail["status"] == "AWAITING_APPROVAL"


def test_recovery_writes_an_audit_record(superadmin, vendor_a, make_case):
    case = make_case(vendor_a)
    db = SessionLocal()
    try:
        row = db.get(Case, case["id"])
        row.status = CaseStatus.ANALYZING.value
        db.add(row)
        db.commit()
        recover_orphaned_analyses(db)
    finally:
        db.close()

    rows = superadmin.get(
        f"/api/audit?action=STATUS_CHANGED&object_id={case['id']}&page_size=50"
    ).json()["items"]
    assert any("interrupted" in (r.get("detail") or "").lower() for r in rows)
