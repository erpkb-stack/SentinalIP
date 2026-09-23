"""Agent pipeline, confidence banding, evidence and enforcement lifecycle."""
from __future__ import annotations

import pytest

from app.constants import risk_level_for


# --------------------------------------------------------------------------- #
# Confidence banding
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("score,expected", [
    (0, "LOW"), (39, "LOW"), (40, "MEDIUM"), (69, "MEDIUM"),
    (70, "HIGH"), (89, "HIGH"), (90, "CRITICAL"), (100, "CRITICAL"),
    (39.4, "LOW"), (89.6, "CRITICAL"), (None, "LOW"), (250, "CRITICAL"), (-5, "LOW"),
])
def test_confidence_bands(score, expected):
    assert risk_level_for(score) == expected


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
def test_pipeline_runs_all_four_agents_in_order(vendor_a, make_case):
    case = make_case(vendor_a, run_analysis=True)
    detail = vendor_a.get(f"/api/cases/{case['id']}").json()

    agents = detail["agents"]
    assert [a["agent"] for a in agents] == [
        "SCOUT", "VERIFICATION", "ENFORCEMENT_STRATEGIST", "FILING_TRACKING"
    ]
    assert [a["status"] for a in agents[:3]] == ["COMPLETE"] * 3
    # Filing deliberately stops at the gate rather than completing.
    assert agents[3]["status"] == "AWAITING_APPROVAL"


def test_pipeline_produces_confidence_and_a_recommendation(vendor_a, make_case):
    case = make_case(vendor_a, run_analysis=True)
    detail = vendor_a.get(f"/api/cases/{case['id']}").json()

    assert detail["ai_confidence"] is not None
    assert 0 <= detail["ai_confidence"] <= 100
    assert detail["risk_level"] == risk_level_for(detail["ai_confidence"])
    assert detail["recommended_action"]
    assert detail["enforcement"]["reasoning"]
    assert detail["confidence_disclaimer"]


def test_pipeline_collects_evidence_with_checksums(vendor_a, make_case):
    case = make_case(vendor_a, run_analysis=True)
    evidence = vendor_a.get(f"/api/cases/{case['id']}/evidence").json()
    assert len(evidence) >= 3
    for item in evidence:
        assert item["evidence_ref"].startswith("EV-")
        assert item["checksum"]
        assert item["collected_by"]
        assert 0 <= item["strength"] <= 100


def test_verification_reports_contradictory_evidence_too(vendor_a, make_case):
    """A model that only ever argues one way is not an analyst."""
    case = make_case(vendor_a, run_analysis=True)
    detail = vendor_a.get(f"/api/cases/{case['id']}").json()
    findings = detail["findings"]
    assert findings.get("SUPPORTING"), "expected supporting findings"
    # Risks are always recorded, even for a confident recommendation.
    assert findings.get("RISK"), "expected the strategist to record risks"
    assert findings.get("ALTERNATIVE"), "expected rejected alternatives"


def test_agent_run_output_hides_internal_keys(vendor_a, make_case):
    case = make_case(vendor_a, run_analysis=True)
    detail = vendor_a.get(f"/api/cases/{case['id']}").json()
    run_id = detail["agents"][0]["run_id"]
    run = vendor_a.get(f"/api/cases/{case['id']}/agents/{run_id}").json()
    assert all(not k.startswith("_") for k in run["output"])
    # No system prompt is ever returned.
    assert "system" not in str(run["output"]).lower() or "prompt" not in str(run["output"]).lower()


def test_mock_provider_is_deterministic(vendor_a, make_case):
    """The same listing must produce the same analysis - a reproducible demo."""
    url = "https://example.com/itm/deterministic-check"
    a = make_case(vendor_a, run_analysis=True, listing_url=url, product_sku="DET-1")
    b = make_case(vendor_a, run_analysis=True, listing_url=url, product_sku="DET-1")
    da = vendor_a.get(f"/api/cases/{a['id']}").json()
    db_ = vendor_a.get(f"/api/cases/{b['id']}").json()
    assert da["ai_confidence"] == db_["ai_confidence"]
    assert da["recommended_action"] == db_["recommended_action"]


def test_analysis_is_not_offered_after_filing(vendor_a, make_case):
    case = make_case(vendor_a, run_analysis=True)
    vendor_a.post(f"/api/cases/{case['id']}/approve", json={"comments": "ok"})
    res = vendor_a.post(f"/api/cases/{case['id']}/analyze")
    assert res.status_code == 409


# --------------------------------------------------------------------------- #
# Evidence handling
# --------------------------------------------------------------------------- #
def test_evidence_upload_rejects_a_disallowed_extension(vendor_a, make_case):
    case = make_case(vendor_a)
    res = vendor_a.post(
        f"/api/cases/{case['id']}/evidence",
        files={"file": ("payload.exe", b"MZ\x90\x00", "application/octet-stream")},
        data={"category": "EXTERNAL_REFERENCE"},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "UPLOAD_REJECTED"


def test_evidence_upload_rejects_content_that_lies_about_its_extension(
    vendor_a, make_case
):
    """A PE binary renamed to .png must not get through."""
    case = make_case(vendor_a)
    res = vendor_a.post(
        f"/api/cases/{case['id']}/evidence",
        files={"file": ("fake.png", b"MZ\x90\x00" + b"\x00" * 64, "image/png")},
        data={"category": "PRODUCT_IMAGE"},
    )
    assert res.status_code == 400
    assert "contents do not match" in res.json()["error"]["message"]


def test_evidence_upload_accepts_a_real_png(vendor_a, make_case):
    case = make_case(vendor_a)
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    res = vendor_a.post(
        f"/api/cases/{case['id']}/evidence",
        files={"file": ("shot.png", png, "image/png")},
        data={"category": "PRODUCT_IMAGE", "title": "Listing screenshot"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["checksum"]
    assert body["download_url"]
    assert vendor_a.get(body["download_url"]).status_code == 200


def test_evidence_is_archived_not_deleted(vendor_a, make_case):
    case = make_case(vendor_a, run_analysis=True)
    evidence = vendor_a.get(f"/api/cases/{case['id']}/evidence").json()
    target = evidence[0]

    res = vendor_a.post(f"/api/evidence/{target['id']}/archive",
                        data={"reason": "Superseded by a clearer capture."})
    assert res.status_code == 200
    assert res.json()["is_archived"] is True

    still_there = vendor_a.get(f"/api/cases/{case['id']}/evidence").json()
    assert target["evidence_ref"] in [e["evidence_ref"] for e in still_there]


def test_evidence_review_states_are_recorded(vendor_a, make_case):
    case = make_case(vendor_a, run_analysis=True)
    evidence = vendor_a.get(f"/api/cases/{case['id']}/evidence").json()
    res = vendor_a.put(f"/api/evidence/{evidence[0]['id']}/review",
                       json={"relevance": "DISPUTED", "note": "Seller contests this."})
    assert res.status_code == 200
    assert res.json()["relevance"] == "DISPUTED"


# --------------------------------------------------------------------------- #
# Enforcement tracking
# --------------------------------------------------------------------------- #
def test_enforcement_timeline_reflects_the_gate(vendor_a, make_case):
    case = make_case(vendor_a, run_analysis=True)
    steps = vendor_a.get(f"/api/cases/{case['id']}/enforcement/timeline").json()
    by_key = {s["key"]: s for s in steps}
    assert by_key["DRAFT"]["done"] is True
    assert by_key["HUMAN_APPROVED"]["done"] is False
    assert by_key["SUBMITTED"]["done"] is False

    vendor_a.post(f"/api/cases/{case['id']}/approve", json={"comments": "ok"})
    steps = vendor_a.get(f"/api/cases/{case['id']}/enforcement/timeline").json()
    by_key = {s["key"]: s for s in steps}
    assert by_key["HUMAN_APPROVED"]["done"] is True
    assert by_key["SUBMITTED"]["done"] is True


def test_marketplace_response_moves_the_case_on(superadmin, vendor_a, make_case):
    case = make_case(vendor_a, run_analysis=True)
    vendor_a.post(f"/api/cases/{case['id']}/approve", json={"comments": "ok"})
    res = superadmin.post(f"/api/cases/{case['id']}/enforcement/response?result=LISTING_REMOVED")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["result"] == "LISTING_REMOVED"
    assert body["status"] == "RESOLVED"
    assert "DEMO / SIMULATED" in (body["marketplace_response"] or "")


def test_filings_are_labelled_simulated(vendor_a, make_case):
    case = make_case(vendor_a, run_analysis=True)
    vendor_a.post(f"/api/cases/{case['id']}/approve", json={"comments": "ok"})
    detail = vendor_a.get(f"/api/cases/{case['id']}").json()
    assert detail["enforcement"]["is_simulated"] is True
    assert detail["enforcement"]["submission_reference"].startswith("SIM-")
