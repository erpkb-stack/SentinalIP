from __future__ import annotations

import datetime as dt
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.constants import (
    GLOBAL_ROLES,
    VENDOR_SCOPED_ROLES,
    RoleCode,
    UserStatus,
    VendorStatus,
)
from app.schemas.common import ORMModel


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    remember_me: bool = False


class LoginResponse(BaseModel):
    success: bool = True
    access_token: str
    token_type: str = "bearer"
    expires_at: dt.datetime
    csrf_token: str
    user: "MeResponse"


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #
class RoleOut(ORMModel):
    id: int
    code: str
    name: str
    description: Optional[str] = None
    permissions: List[str] = []
    requires_vendor: bool = False


# --------------------------------------------------------------------------- #
# Vendors
# --------------------------------------------------------------------------- #
class VendorBase(BaseModel):
    name: str = Field(min_length=2, max_length=191)
    legal_name: Optional[str] = Field(default=None, max_length=255)
    contact_name: Optional[str] = Field(default=None, max_length=191)
    contact_email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=64)
    country: Optional[str] = Field(default=None, max_length=96)
    website: Optional[str] = Field(default=None, max_length=512)
    industry: Optional[str] = Field(default=None, max_length=128)
    sla_policy_id: Optional[int] = None
    autonomy_tier: int = Field(default=1, ge=0, le=4)
    status: str = VendorStatus.ACTIVE.value
    notes: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in VendorStatus.values():
            raise ValueError(f"status must be one of {VendorStatus.values()}")
        return v


class VendorCreate(VendorBase):
    pass


class VendorUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=191)
    legal_name: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    phone: Optional[str] = None
    country: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    sla_policy_id: Optional[int] = None
    autonomy_tier: Optional[int] = Field(default=None, ge=0, le=4)
    status: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v is not None and v not in VendorStatus.values():
            raise ValueError(f"status must be one of {VendorStatus.values()}")
        return v


class VendorOut(ORMModel):
    id: int
    name: str
    legal_name: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    phone: Optional[str] = None
    country: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    sla_policy_id: Optional[int] = None
    sla_policy_name: Optional[str] = None
    autonomy_tier: int = 1
    autonomy_label: Optional[str] = None
    status: str
    user_count: int = 0
    open_case_count: int = 0
    created_at: dt.datetime


class VendorOption(BaseModel):
    """Payload for the Vendor dropdown on the Create User form."""
    id: int
    name: str
    status: str
    autonomy_tier: int = 1


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
class UserCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=96)
    last_name: str = Field(min_length=1, max_length=96)
    email: EmailStr
    #: Omit to have the server generate a compliant temporary password.
    password: Optional[str] = Field(default=None, max_length=256)
    role_code: str
    vendor_id: Optional[int] = None
    status: str = UserStatus.ACTIVE.value
    title: Optional[str] = Field(default=None, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=64)
    must_change_password: bool = True

    @field_validator("role_code")
    @classmethod
    def _role(cls, v: str) -> str:
        if v not in RoleCode.values():
            raise ValueError(f"role_code must be one of {RoleCode.values()}")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in UserStatus.values():
            raise ValueError(f"status must be one of {UserStatus.values()}")
        return v

    @model_validator(mode="after")
    def _vendor_rules(self):
        """A Vendor User MUST have a vendor; a Super Admin MUST NOT.

        An Admin may optionally have one: with a vendor they administer that
        tenant only; without one they are a platform administrator.
        """
        if self.role_code in VENDOR_SCOPED_ROLES and self.vendor_id is None:
            raise ValueError("A Vendor User must be assigned to a vendor.")
        if self.role_code in GLOBAL_ROLES and self.vendor_id is not None:
            raise ValueError(
                f"{self.role_code} is a platform-wide role and cannot be tied to a vendor."
            )
        return self


class UserUpdate(BaseModel):
    first_name: Optional[str] = Field(default=None, min_length=1, max_length=96)
    last_name: Optional[str] = Field(default=None, min_length=1, max_length=96)
    email: Optional[EmailStr] = None
    role_code: Optional[str] = None
    vendor_id: Optional[int] = None
    status: Optional[str] = None
    title: Optional[str] = None
    phone: Optional[str] = None
    password: Optional[str] = Field(default=None, max_length=256)

    @field_validator("role_code")
    @classmethod
    def _role(cls, v):
        if v is not None and v not in RoleCode.values():
            raise ValueError(f"role_code must be one of {RoleCode.values()}")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v is not None and v not in UserStatus.values():
            raise ValueError(f"status must be one of {UserStatus.values()}")
        return v


class UserOut(ORMModel):
    id: int
    first_name: str
    last_name: str
    full_name: str = ""
    email: str
    role_code: str = ""
    role_label: str = ""
    vendor_id: Optional[int] = None
    vendor_name: Optional[str] = None
    status: str
    title: Optional[str] = None
    phone: Optional[str] = None
    last_login_at: Optional[dt.datetime] = None
    must_change_password: bool = False
    created_at: dt.datetime


class UserCreatedResponse(BaseModel):
    success: bool = True
    user: UserOut
    #: Returned exactly once, only when the server generated it.
    temporary_password: Optional[str] = None
    message: str = "User created."


class MeResponse(BaseModel):
    id: int
    email: str
    full_name: str
    first_name: str
    last_name: str
    role_code: str
    role_label: str
    permissions: List[str] = []
    vendor_id: Optional[int] = None
    vendor_name: Optional[str] = None
    autonomy_tier: Optional[int] = None
    is_global: bool = False
    must_change_password: bool = False
    last_login_at: Optional[dt.datetime] = None


LoginResponse.model_rebuild()
