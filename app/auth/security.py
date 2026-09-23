"""Password hashing, JWT issuing/verification, CSRF tokens.

No secret ever leaves this module in plaintext, and no hash is ever returned
by an API schema.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import re
import secrets
from typing import Any, Dict, Optional

import bcrypt
import jwt

from app.config import settings
from app.errors import SessionExpired, ValidationFailed

# The hashing libraries are used directly rather than through passlib: passlib
# has been unmaintained since 2020, breaks against bcrypt 4.x ("error reading
# bcrypt version") and is not a safe bet on Python 3.12+. Only one scheme is in
# use, so the abstraction bought nothing.

#: bcrypt silently truncates beyond 72 bytes - reject rather than truncate,
#: which would make two different long passwords interchangeable.
BCRYPT_MAX_BYTES = 72
BCRYPT_ROUNDS = 12

SCHEME = (
    settings.password_hash_scheme.lower()
    if settings.password_hash_scheme.lower() in ("bcrypt", "argon2")
    else "bcrypt"
)


def _argon2_hasher():
    from argon2 import PasswordHasher

    return PasswordHasher()


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    """Hash with the configured scheme. The scheme is identifiable from the
    hash prefix, so `verify_password` keeps working across a scheme change."""
    if SCHEME == "argon2":
        return _argon2_hasher().hash(password)
    if len(password.encode("utf-8")) > BCRYPT_MAX_BYTES:
        raise ValidationFailed("Password must be 72 bytes or fewer.")
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    ).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time check against either scheme. Never raises."""
    if not plain or not hashed:
        return False
    try:
        if hashed.startswith("$argon2"):
            from argon2.exceptions import VerificationError, VerifyMismatchError

            try:
                return _argon2_hasher().verify(hashed, plain)
            except (VerifyMismatchError, VerificationError):
                return False
        encoded = plain.encode("utf-8")
        if len(encoded) > BCRYPT_MAX_BYTES:
            return False
        return bcrypt.checkpw(encoded, hashed.encode("utf-8"))
    except Exception:
        return False


_PASSWORD_RULES = [
    (r".{%d,}" % settings.password_min_length,
     f"at least {settings.password_min_length} characters"),
    (r"[a-z]", "a lowercase letter"),
    (r"[A-Z]", "an uppercase letter"),
    (r"[0-9]", "a number"),
    (r"[^A-Za-z0-9]", "a symbol"),
]

_COMMON = {
    "password", "password1", "passw0rd", "welcome1", "letmein", "qwerty123",
    "admin123", "changeme", "123456789", "sentinel123",
}


def validate_password_strength(password: str) -> None:
    """Raise ValidationFailed with every unmet rule (not just the first)."""
    missing = [label for pattern, label in _PASSWORD_RULES
               if not re.search(pattern, password or "")]
    if (password or "").lower() in _COMMON:
        missing.append("something less common than a well-known password")
    if missing:
        raise ValidationFailed(
            "Password must contain " + ", ".join(missing) + ".",
            details={"unmet": missing},
        )


def generate_temporary_password() -> str:
    """A compliant random password for admin-created accounts."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    lower = "abcdefghijkmnopqrstuvwxyz"
    digits = "23456789"
    symbols = "!@#$%^&*?"
    core = [
        secrets.choice(alphabet), secrets.choice(lower),
        secrets.choice(digits), secrets.choice(symbols),
    ]
    pool = alphabet + lower + digits + symbols
    core += [secrets.choice(pool) for _ in range(max(8, settings.password_min_length) - 4)]
    secrets.SystemRandom().shuffle(core)
    return "".join(core)


# --------------------------------------------------------------------------- #
# JWT
# --------------------------------------------------------------------------- #
def create_access_token(
    *,
    user_id: int,
    email: str,
    role_code: str,
    vendor_id: Optional[int],
    token_version: int = 0,
    remember: bool = False,
    extra: Optional[Dict[str, Any]] = None,
) -> tuple[str, dt.datetime]:
    minutes = (
        settings.remember_me_expire_minutes if remember
        else settings.access_token_expire_minutes
    )
    now = dt.datetime.now(dt.timezone.utc)
    expires = now + dt.timedelta(minutes=minutes)
    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "role": role_code,
        "vendor_id": vendor_id,
        "tv": token_version,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": secrets.token_urlsafe(12),
        "iss": "sentinel-ip-ai",
    }
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, expires.replace(tzinfo=None)


def decode_access_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer="sentinel-ip-ai",
            options={"require": ["exp", "sub", "iat"]},
        )
    except jwt.ExpiredSignatureError:
        raise SessionExpired()
    except jwt.InvalidTokenError:
        raise SessionExpired("Your session is not valid. Please sign in again.")


# --------------------------------------------------------------------------- #
# CSRF (double-submit cookie)
# --------------------------------------------------------------------------- #
def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_tokens_match(cookie_token: Optional[str], header_token: Optional[str]) -> bool:
    if not cookie_token or not header_token:
        return False
    return hmac.compare_digest(cookie_token, header_token)


# --------------------------------------------------------------------------- #
# Misc
# --------------------------------------------------------------------------- #
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
