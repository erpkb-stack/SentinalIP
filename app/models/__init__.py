"""SQLAlchemy models.

Importing this package registers every mapper, which is what Alembic's
autogenerate and `Base.metadata.create_all` rely on.
"""
from app.database import Base  # noqa: F401
from app.models.ai import AiAgentRun, AiFinding  # noqa: F401
from app.models.cases import Case, CaseAssignment, Evidence  # noqa: F401
from app.models.catalog import Listing, Marketplace, Product, SlaPolicy  # noqa: F401
from app.models.enforcement import Approval, EnforcementAction  # noqa: F401
from app.models.identity import Role, User, UserVendorAssignment, Vendor  # noqa: F401
from app.models.system import AuditLog, Notification  # noqa: F401

__all__ = [
    "Base",
    "Role",
    "User",
    "Vendor",
    "UserVendorAssignment",
    "Marketplace",
    "SlaPolicy",
    "Product",
    "Listing",
    "Case",
    "CaseAssignment",
    "Evidence",
    "AiAgentRun",
    "AiFinding",
    "EnforcementAction",
    "Approval",
    "AuditLog",
    "Notification",
]
