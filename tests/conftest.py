"""Test fixtures.

The suite runs against an isolated SQLite database so it never touches the
MySQL instance the application uses. The schema is identical - the models are
written to be portable - so these tests exercise the same code paths.
"""
from __future__ import annotations

import os
import tempfile

import pytest

# Must be set before app.config is imported anywhere.
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="sentinel-test-"), "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ["APP_ENV"] = "test"
os.environ["DEBUG"] = "true"
os.environ["SECRET_KEY"] = "test-only-secret-key-not-used-anywhere-else-0123456789"
os.environ["AI_PROVIDER"] = "mock"
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="sentinel-uploads-")
os.environ["LOGIN_RATE_LIMIT_ATTEMPTS"] = "1000"
os.environ["SEED_DEFAULT_PASSWORD"] = "TestPassw0rd!2026"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.auth.security import hash_password  # noqa: E402
from app.constants import (  # noqa: E402
    ROLE_LABELS,
    ROLE_PERMISSIONS,
    RoleCode,
    UserStatus,
    VendorStatus,
)
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Marketplace,
    Role,
    SlaPolicy,
    User,
    UserVendorAssignment,
    Vendor,
)

TEST_PASSWORD = "TestPassw0rd!2026"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(engine)
    yield
    # The database is a throwaway temp file; SQLite cannot ALTER away the
    # circular foreign keys, so deleting the file is the reliable teardown.
    engine.dispose()
    try:
        os.remove(_TMP_DB)
    except OSError:
        pass


@pytest.fixture(scope="session")
def seeded(_schema):
    """Two vendors, one user of each kind. Returns a lookup dict."""
    db = SessionLocal()
    try:
        roles = {}
        for code, requires_vendor in [
            (RoleCode.SUPER_ADMIN.value, False),
            (RoleCode.ADMIN.value, False),
            (RoleCode.VENDOR_USER.value, True),
        ]:
            role = Role(
                code=code, name=ROLE_LABELS[code],
                permissions=ROLE_PERMISSIONS[code], requires_vendor=requires_vendor,
            )
            db.add(role)
            roles[code] = role
        db.flush()

        policy = SlaPolicy(
            name="Test SLA", response_hours=48, resolution_hours=168,
            at_risk_percent=75, is_default=True, is_active=True,
        )
        db.add(policy)

        marketplace = Marketplace(code="TESTMP", name="Test Marketplace")
        db.add(marketplace)
        db.flush()

        vendor_a = Vendor(name="Vendor A", status=VendorStatus.ACTIVE.value,
                          autonomy_tier=1, sla_policy_id=policy.id)
        vendor_b = Vendor(name="Vendor B", status=VendorStatus.ACTIVE.value,
                          autonomy_tier=1, sla_policy_id=policy.id)
        db.add_all([vendor_a, vendor_b])
        db.flush()

        def make_user(email, first, role_code, vendor):
            u = User(
                email=email, first_name=first, last_name="Tester",
                password_hash=hash_password(TEST_PASSWORD),
                role_id=roles[role_code].id,
                vendor_id=vendor.id if vendor else None,
                status=UserStatus.ACTIVE.value,
            )
            db.add(u)
            db.flush()
            if vendor:
                db.add(UserVendorAssignment(
                    user_id=u.id, vendor_id=vendor.id, is_primary=True
                ))
            return u

        superadmin = make_user("super@test.local", "Super", RoleCode.SUPER_ADMIN.value, None)
        admin = make_user("admin@test.local", "Admin", RoleCode.ADMIN.value, None)
        user_a = make_user("a@test.local", "Alice", RoleCode.VENDOR_USER.value, vendor_a)
        user_b = make_user("b@test.local", "Bob", RoleCode.VENDOR_USER.value, vendor_b)
        db.commit()

        return {
            "vendor_a_id": vendor_a.id,
            "vendor_b_id": vendor_b.id,
            "marketplace_id": marketplace.id,
            "policy_id": policy.id,
            "superadmin": superadmin.email,
            "admin": admin.email,
            "user_a": user_a.email,
            "user_b": user_b.email,
            "user_a_id": user_a.id,
            "user_b_id": user_b.id,
        }
    finally:
        db.close()


class Client:
    """TestClient wrapper that carries the session cookie and CSRF header."""

    def __init__(self, email: str | None = None):
        self.client = TestClient(app)
        self.email = email
        if email:
            self.login(email)

    def login(self, email: str, password: str = TEST_PASSWORD):
        res = self.client.post(
            "/api/auth/login", json={"email": email, "password": password}
        )
        assert res.status_code == 200, res.text
        self.client.headers["X-CSRF-Token"] = res.json()["csrf_token"]
        return res

    def __getattr__(self, name):
        return getattr(self.client, name)


@pytest.fixture
def anon():
    return TestClient(app)


@pytest.fixture
def superadmin(seeded):
    return Client(seeded["superadmin"])


@pytest.fixture
def admin(seeded):
    return Client(seeded["admin"])


@pytest.fixture
def vendor_a(seeded):
    return Client(seeded["user_a"])


@pytest.fixture
def vendor_b(seeded):
    return Client(seeded["user_b"])


@pytest.fixture
def make_case(seeded):
    """Create a case for a vendor via the API and return its payload."""
    def _make(client: Client, vendor_id=None, run_analysis=False, **overrides):
        payload = {
            "product_name": "Test Widget",
            "product_sku": f"SKU-{os.urandom(3).hex()}",
            "brand": "TestBrand",
            "trademark": "TESTMARK",
            "msrp": 100.0,
            "marketplace_id": seeded["marketplace_id"],
            "listing_url": "https://example.com/itm/" + os.urandom(3).hex(),
            "seller_name": "SuspectSeller",
            "listing_price": 22.0,
            "infringement_type": "COUNTERFEIT",
            "run_analysis": run_analysis,
        }
        if vendor_id is not None:
            payload["vendor_id"] = vendor_id
        payload.update(overrides)
        res = client.post("/api/cases", json=payload)
        assert res.status_code == 201, res.text
        return res.json()
    return _make
