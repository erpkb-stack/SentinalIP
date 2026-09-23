"""Server-rendered page shells.

The New Case form regressed in the field: its required "Infringement Type"
dropdown was populated by an XHR, so when that request failed the field could
never be satisfied and the case could not be created. These tests assert the
choices are in the HTML the server sends, independent of any JavaScript.
"""
from __future__ import annotations

import pytest

from app.constants import INFRINGEMENT_LABELS, Priority


PAGES = [
    "/dashboard", "/cases", "/my-cases", "/cases/new", "/ai-operations",
    "/evidence", "/enforcement", "/reports", "/audit", "/settings",
]


@pytest.mark.parametrize("path", PAGES)
def test_pages_render_for_a_signed_in_user(vendor_a, path):
    res = vendor_a.get(path)
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


@pytest.mark.parametrize("path", PAGES + ["/admin/users", "/admin/vendors"])
def test_pages_redirect_when_signed_out(anon, path):
    res = anon.get(path, follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"].startswith("/login")


# --------------------------------------------------------------------------- #
# The New Case form must not depend on JavaScript for its choices
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value,label", sorted(INFRINGEMENT_LABELS.items()))
def test_infringement_options_are_server_rendered(vendor_a, value, label):
    html = vendor_a.get("/cases/new").text
    assert f'<option value="{value}">' in html, (
        f"{value} is missing from the New Case HTML - the dropdown would be "
        "empty without a working /api/meta"
    )
    assert label in html


@pytest.mark.parametrize("priority", Priority.values())
def test_priority_options_are_server_rendered(vendor_a, priority):
    html = vendor_a.get("/cases/new").text
    assert f'<option value="{priority}"' in html


def test_priority_defaults_to_medium(vendor_a):
    assert '<option value="MEDIUM" selected>' in vendor_a.get("/cases/new").text


def test_marketplaces_are_server_rendered(vendor_a, seeded):
    """A case needs a marketplace, so the list ships with the page."""
    html = vendor_a.get("/cases/new").text
    assert "Test Marketplace" in html
    assert f'<option value="{seeded["marketplace_id"]}">' in html


def test_currency_options_are_server_rendered(vendor_a):
    html = vendor_a.get("/cases/new").text
    assert '<option value="USD" selected>' in html
    assert '<option value="EUR">' in html


# --------------------------------------------------------------------------- #
# The vendor picker is decided server-side, not revealed by a fetch
# --------------------------------------------------------------------------- #
def test_platform_user_gets_a_populated_vendor_picker(superadmin, seeded):
    html = superadmin.get("/cases/new").text
    assert 'id="vendorField"' in html
    # Visible: no `hidden` attribute on that field for a global user.
    field = html.split('id="vendorField"')[1].split(">")[0]
    assert "hidden" not in field
    assert "Vendor A" in html and "Vendor B" in html


def test_vendor_user_does_not_see_the_vendor_picker(vendor_a):
    html = vendor_a.get("/cases/new").text
    field = html.split('id="vendorField"')[1].split(">")[0]
    assert "hidden" in field, "a tenant user must not be asked to pick a vendor"


def test_vendor_user_page_leaks_no_other_tenant(vendor_a):
    """The form must not name a vendor this user cannot see."""
    html = vendor_a.get("/cases/new").text
    assert "Vendor B" not in html


# --------------------------------------------------------------------------- #
# Navigation is role-aware
# --------------------------------------------------------------------------- #
def test_administration_menu_is_hidden_from_vendor_users(vendor_a):
    html = vendor_a.get("/dashboard").text
    assert "/admin/users" not in html
    assert "/admin/vendors" not in html


def test_administration_menu_is_present_for_admins(superadmin):
    html = superadmin.get("/dashboard").text
    assert "/admin/users" in html
    assert "/admin/vendors" in html


def test_pages_contain_no_inline_script(vendor_a):
    """CSP is `script-src 'self'` - an inline <script> would silently not run,
    which is how the login page broke once already."""
    for path in ["/dashboard", "/cases/new", "/cases", "/admin/users"]:
        html = vendor_a.get(path).text
        for chunk in html.split("<script")[1:]:
            attrs = chunk.split(">")[0]
            assert "src=" in attrs, f"{path} has an inline <script>, blocked by CSP"


def test_login_page_has_no_inline_script(anon):
    html = anon.get("/login").text
    for chunk in html.split("<script")[1:]:
        assert "src=" in chunk.split(">")[0], "the login page has an inline <script>"
