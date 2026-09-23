from app.api import (  # noqa: F401
    admin,
    agents,
    audit,
    auth,
    cases,
    dashboard,
    enforcement,
    evidence,
    meta,
    notifications,
    pages,
    users,
    vendors,
    ws,
)

API_ROUTERS = [
    auth.router,
    meta.router,
    dashboard.router,
    vendors.router,
    users.router,
    cases.router,
    evidence.router,
    enforcement.router,
    agents.router,
    audit.router,
    notifications.router,
    admin.router,
]

__all__ = ["API_ROUTERS", "pages", "ws"]
