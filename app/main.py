"""Sentinel IP AI - application entry point."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import API_ROUTERS, pages, ws
from app.config import BASE_DIR, settings
from app.errors import register_exception_handlers
from app.logging_config import configure_logging, get_logger
from app.middleware import RequestContextMiddleware, SecurityHeadersMiddleware

configure_logging(settings.log_level)
logger = get_logger("sentinel")


def _startup_checks() -> None:
    """Fail loudly on an insecure production configuration."""
    problems = []
    if settings.is_production:
        if "change" in settings.secret_key.lower() or len(settings.secret_key) < 32:
            problems.append("SECRET_KEY is unset or too weak for production.")
        if not settings.cookie_secure:
            problems.append("COOKIE_SECURE must be true in production (HTTPS only).")
        if settings.debug:
            problems.append("DEBUG must be false in production.")
    if problems:
        raise RuntimeError(
            "Refusing to start with an insecure configuration:\n  - "
            + "\n  - ".join(problems)
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _startup_checks()
    os.makedirs(settings.upload_dir, exist_ok=True)
    logger.info(
        "%s v%s starting (env=%s, ai_provider=%s, db=%s)",
        settings.app_name, __version__, settings.app_env, settings.ai_provider,
        settings.sqlalchemy_url.split("@")[-1],
    )
    # Bring SLA states up to date after downtime, and recover any case left
    # mid-pipeline by a restart.
    try:
        from app.database import session_scope
        from app.services import sla
        from app.services.recovery import recover_orphaned_analyses
        with session_scope() as db:
            changed = sla.refresh_all(db)
            recovered = recover_orphaned_analyses(db)
        if changed:
            logger.info("SLA sweep updated %s case(s).", changed)
        if recovered:
            logger.warning(
                "Reset %s case(s) left in ANALYZING by a previous run; "
                "re-run the analysis on them.", recovered,
            )
    except Exception:
        logger.warning("Startup sweep skipped (database not ready?).")
    yield
    logger.info("%s shutting down.", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    description=(
        f"{settings.app_tagline}\n\n"
        "Multi-agent IP enforcement platform. Tenant isolation and human "
        "approval are enforced server-side as security boundaries."
    ),
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs" if not settings.is_production else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if not settings.is_production else None,
)

# ---- middleware (outermost first) ----
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Correlation-ID"],
)

register_exception_handlers(app)

# ---- routes ----
for router in API_ROUTERS:
    app.include_router(router)
app.include_router(ws.router)
app.include_router(pages.router)

app.mount(
    "/static",
    StaticFiles(directory=os.path.join(BASE_DIR, "frontend", "static")),
    name="static",
)


@app.get("/api/health", tags=["meta"], include_in_schema=True)
def health():
    """Liveness probe. Deliberately exposes nothing about configuration."""
    return {"status": "ok", "app": settings.app_name, "version": __version__}
