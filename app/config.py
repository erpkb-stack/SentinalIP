"""Application configuration.

All configuration comes from environment variables (or a local `.env` file).
Secrets are never hard-coded and are never returned by any API.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import List, Optional
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------------- app ----------------
    app_name: str = "Sentinel IP AI"
    app_tagline: str = "AI-Powered IP Enforcement at Counterfeit Speed."
    app_env: str = "development"
    debug: bool = True
    log_level: str = "INFO"
    base_dir: str = BASE_DIR

    # ---------------- database ----------------
    database_url: Optional[str] = None
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "sentinel_ip"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle: int = 1800

    # ---------------- security ----------------
    secret_key: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    remember_me_expire_minutes: int = 60 * 24 * 7
    password_hash_scheme: str = "bcrypt"
    cookie_name: str = "sentinel_session"
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    csrf_cookie_name: str = "sentinel_csrf"

    login_rate_limit_attempts: int = 8
    login_rate_limit_window_seconds: int = 300
    account_lockout_attempts: int = 10
    account_lockout_minutes: int = 15
    password_min_length: int = 12

    #: Comma-separated in .env. Bound as a plain string on purpose: a `List[str]`
    #: field is "complex" to pydantic-settings, which JSON-decodes it inside the
    #: dotenv source - before any validator can run - so `a,b` raises a
    #: SettingsError at import time. The parsed list is the `cors_origins`
    #: property below.
    cors_origins_raw: str = Field(
        default="http://localhost:8000,http://127.0.0.1:8000",
        validation_alias="cors_origins",
    )

    # ---------------- uploads ----------------
    upload_dir: str = os.path.join(BASE_DIR, "uploads")
    max_upload_mb: int = 25
    #: Comma-separated in .env - see the note on `cors_origins_raw`.
    allowed_upload_extensions_raw: str = Field(
        default=".png,.jpg,.jpeg,.gif,.webp,.pdf,.txt,.csv,.xls,.xlsx",
        validation_alias="allowed_upload_extensions",
    )

    # ---------------- AI ----------------
    ai_provider: str = "mock"
    ai_model: str = "sentinel-mock-v1"
    ai_request_timeout_seconds: int = 45
    ai_mock_seed: int = 1337
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-pro"

    # ---------------- enforcement ----------------
    default_autonomy_tier: int = 1
    max_autonomy_tier: int = 3
    simulated_filing: bool = True
    #: When a human approves a recommendation, hand it straight to Filing &
    #: Tracking for submission. The approval IS the authorization to file, so
    #: this does not weaken the gate - `submit_action` still refuses without a
    #: matching approval record. Set false to require a separate filing step.
    auto_file_on_approval: bool = True
    default_sla_hours: int = 48
    sla_at_risk_percent: int = 75

    seed_default_password: str = "Sentinel!Demo2026"

    # ------------------------------------------------------------------
    @staticmethod
    def _split_csv(value: str) -> List[str]:
        """Accept `a, b ,c` and JSON-ish `["a","b"]` alike, and ignore blanks."""
        text = (value or "").strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                import json

                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except ValueError:
                pass  # fall through to comma splitting
        return [item.strip() for item in text.split(",") if item.strip()]

    @property
    def cors_origins(self) -> List[str]:
        return self._split_csv(self.cors_origins_raw)

    @property
    def allowed_upload_extensions(self) -> List[str]:
        """Normalised to lowercase, leading-dot form."""
        out = []
        for item in self._split_csv(self.allowed_upload_extensions_raw):
            ext = item.lower()
            out.append(ext if ext.startswith(".") else "." + ext)
        return out

    @property
    def sqlalchemy_url(self) -> str:
        """Effective SQLAlchemy URL.

        Precedence: explicit DATABASE_URL > assembled MySQL URL.
        """
        if self.database_url:
            return self.database_url
        pwd = f":{quote_plus(self.db_password)}" if self.db_password else ""
        return (
            f"mysql+pymysql://{quote_plus(self.db_user)}{pwd}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )

    @property
    def server_url_without_db(self) -> str:
        """Server-level URL (no database selected) - used by init_db to CREATE DATABASE."""
        pwd = f":{quote_plus(self.db_password)}" if self.db_password else ""
        return (
            f"mysql+pymysql://{quote_plus(self.db_user)}{pwd}"
            f"@{self.db_host}:{self.db_port}/?charset=utf8mb4"
        )

    @property
    def is_sqlite(self) -> bool:
        return self.sqlalchemy_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in ("production", "prod")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def _allow_local_demo_domains() -> None:
    """Permit the `.local` demo addresses (user@acme.local) outside production.

    `.local` is a reserved mDNS name, so email-validator rejects it by default -
    correctly, for real mail. The seed accounts in this build use it, so the
    restriction is lifted in non-production environments only.
    """
    if settings.is_production:
        return
    try:
        import email_validator

        for attr in ("SPECIAL_USE_DOMAIN_NAMES",):
            names = getattr(email_validator, attr, None)
            if isinstance(names, list) and "local" in names:
                names.remove("local")
    except Exception:  # pragma: no cover - validator is optional at import time
        pass


_allow_local_demo_domains()
