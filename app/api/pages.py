"""Server-rendered page shells.

Every page except /login requires a valid session server-side. The pages
themselves are thin: they render the chrome and let the browser fetch data
from the REST API, which applies the same authorization a second time.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_optional_user
from app.config import BASE_DIR, settings
from app.constants import (
    AUTONOMY_TIERS,
    EVIDENCE_CATEGORY_LABELS,
    INFRINGEMENT_LABELS,
    ROLE_LABELS,
    Priority,
    VendorStatus,
)
from app.database import get_db
from app.logging_config import get_logger
from app.models import Marketplace, User, Vendor

router = APIRouter(include_in_schema=False)
logger = get_logger(__name__)

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "frontend", "templates"))
templates.env.globals.update(
    APP_NAME=settings.app_name,
    APP_TAGLINE=settings.app_tagline,
    APP_ENV=settings.app_env,
    SIMULATED=settings.simulated_filing,
)


def render(
    request: Request, template: str, user: Optional[User] = None, **ctx
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={
            "user": user,
            "role_label": ROLE_LABELS.get(user.role_code, "") if user else "",
            "is_admin": bool(user and (user.is_global or user.is_admin)),
            "is_platform_admin": bool(user and user.is_global),
            "is_super_admin": bool(user and user.is_super_admin),
            **ctx,
        },
    )


def _guard(request: Request, user: Optional[User]):
    if user is None:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)
    return None


def _admin_guard(request: Request, user: Optional[User]):
    redirect = _guard(request, user)
    if redirect:
        return redirect
    # Platform admins and vendor-scoped admins both reach Administration;
    # what they can actually see there is filtered by the API.
    if not (user.is_global or user.is_admin):
        return RedirectResponse(url="/dashboard?denied=administration", status_code=302)
    return None


# --------------------------------------------------------------------------- #
@router.get("/login")
def login_page(request: Request, user: Optional[User] = Depends(get_optional_user)):
    if user is not None:
        return RedirectResponse(url="/dashboard", status_code=302)
    return render(
        request, "login.html",
        next_url=request.query_params.get("next", "/dashboard"),
        reason=request.query_params.get("reason"),
    )


@router.get("/")
def root(user: Optional[User] = Depends(get_optional_user)):
    return RedirectResponse(url="/dashboard" if user else "/login", status_code=302)


@router.get("/dashboard")
def dashboard_page(request: Request, user: Optional[User] = Depends(get_optional_user)):
    return _guard(request, user) or render(request, "dashboard.html", user, page="dashboard")


@router.get("/cases")
def cases_page(request: Request, user: Optional[User] = Depends(get_optional_user)):
    return _guard(request, user) or render(
        request, "cases.html", user, page="cases", scope="all"
    )


@router.get("/my-cases")
def my_cases_page(request: Request, user: Optional[User] = Depends(get_optional_user)):
    return _guard(request, user) or render(
        request, "cases.html", user, page="my-cases", scope="mine"
    )


@router.get("/cases/new")
def new_case_page(
    request: Request,
    user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Render the form with its options already in the HTML.

    The choices here are the ones a case cannot be created without, so they are
    server-rendered rather than fetched. A form whose required dropdown is
    populated by an XHR becomes unusable the moment that request fails, with no
    way for the user to recover - which is exactly what happened in the field.
    JavaScript now only refreshes these lists; it never supplies them.
    """
    redirect = _guard(request, user)
    if redirect:
        return redirect

    marketplaces = []
    try:
        marketplaces = db.execute(
            select(Marketplace)
            .where(Marketplace.is_active.is_(True))
            .order_by(Marketplace.name)
        ).scalars().all()
    except Exception:  # pragma: no cover - the free-text field is the fallback
        logger.warning("Could not load marketplaces for the New Case form.")

    # A platform user must name the tenant the case belongs to, so that choice
    # is rendered server-side too rather than revealed by a later fetch.
    vendors = []
    if user.is_global:
        try:
            vendors = db.execute(
                select(Vendor)
                .where(Vendor.status == VendorStatus.ACTIVE.value)
                .order_by(Vendor.name)
            ).scalars().all()
        except Exception:  # pragma: no cover
            logger.warning("Could not load vendors for the New Case form.")

    return render(
        request, "case_new.html", user, page="create-case",
        infringement_types=INFRINGEMENT_LABELS,
        priorities=[(p, p.title()) for p in Priority.values()],
        evidence_categories=EVIDENCE_CATEGORY_LABELS,
        marketplaces=marketplaces,
        vendors=vendors,
        needs_vendor=user.is_global,
        currencies=["USD", "EUR", "GBP", "CAD", "AUD", "MXN", "JPY", "CNY"],
    )


@router.get("/cases/{case_id}")
def case_detail_page(
    case_id: int, request: Request, user: Optional[User] = Depends(get_optional_user)
):
    return _guard(request, user) or render(
        request, "case_detail.html", user, page="cases", case_id=case_id
    )


@router.get("/ai-operations")
def ai_ops_page(request: Request, user: Optional[User] = Depends(get_optional_user)):
    return _guard(request, user) or render(request, "ai_ops.html", user, page="ai-ops")


@router.get("/evidence")
def evidence_page(request: Request, user: Optional[User] = Depends(get_optional_user)):
    return _guard(request, user) or render(
        request, "evidence.html", user, page="evidence"
    )


@router.get("/enforcement")
def enforcement_page(request: Request, user: Optional[User] = Depends(get_optional_user)):
    return _guard(request, user) or render(
        request, "enforcement.html", user, page="enforcement"
    )


@router.get("/reports")
def reports_page(request: Request, user: Optional[User] = Depends(get_optional_user)):
    return _guard(request, user) or render(request, "reports.html", user, page="reports")


@router.get("/audit")
def audit_page(request: Request, user: Optional[User] = Depends(get_optional_user)):
    return _guard(request, user) or render(request, "audit.html", user, page="audit")


@router.get("/settings")
def settings_page(request: Request, user: Optional[User] = Depends(get_optional_user)):
    return _guard(request, user) or render(request, "settings.html", user, page="settings")


# ---------------- administration ----------------
@router.get("/admin/users")
def admin_users_page(request: Request, user: Optional[User] = Depends(get_optional_user)):
    return _admin_guard(request, user) or render(
        request, "admin_users.html", user, page="admin-users"
    )


@router.get("/admin/vendors")
def admin_vendors_page(request: Request, user: Optional[User] = Depends(get_optional_user)):
    return _admin_guard(request, user) or render(
        request, "admin_vendors.html", user, page="admin-vendors"
    )


@router.get("/admin/roles")
def admin_roles_page(request: Request, user: Optional[User] = Depends(get_optional_user)):
    return _admin_guard(request, user) or render(
        request, "admin_roles.html", user, page="admin-roles"
    )


@router.get("/admin/sla")
def admin_sla_page(request: Request, user: Optional[User] = Depends(get_optional_user)):
    return _admin_guard(request, user) or render(
        request, "admin_sla.html", user, page="admin-sla"
    )


@router.get("/admin/autonomy")
def admin_autonomy_page(request: Request, user: Optional[User] = Depends(get_optional_user)):
    return _admin_guard(request, user) or render(
        request, "admin_autonomy.html", user, page="admin-autonomy",
        tiers=AUTONOMY_TIERS,
    )
