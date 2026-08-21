"""
Organization contract models for Bifrost.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

if TYPE_CHECKING:
    pass


# ==================== ORGANIZATION MODELS ====================


class Organization(BaseModel):
    """Organization entity (response model)"""

    id: str = Field(..., description="Organization ID (GUID)")
    name: str = Field(..., min_length=1, max_length=200)
    domain: str | None = Field(
        None, description="Email domain for auto-provisioning users (e.g., 'acme.com')"
    )
    is_active: bool = Field(default=True)
    is_provider: bool = Field(
        default=False,
        description="Provider organization (immutable, cannot be deleted)",
    )
    created_at: datetime
    created_by: str
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreateOrganizationRequest(BaseModel):
    """Request model for creating an organization"""

    name: str = Field(..., min_length=1, max_length=200)
    domain: str | None = Field(
        None, description="Email domain for auto-provisioning users (e.g., 'acme.com')"
    )

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v):
        """Validate domain format (no @ symbol, just the domain)"""
        if v is not None:
            v = v.strip().lower()
            if "@" in v:
                raise ValueError(
                    "Domain should not include '@' symbol (e.g., use 'acme.com' not '@acme.com')"
                )
            if not v or "." not in v:
                raise ValueError("Domain must be a valid format (e.g., 'acme.com')")
        return v


class UpdateOrganizationRequest(BaseModel):
    """Request model for updating an organization"""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    domain: str | None = Field(
        default=None, description="Email domain for auto-provisioning users"
    )
    is_active: bool | None = None

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v):
        """Validate domain format (no @ symbol, just the domain)"""
        if v is not None:
            v = v.strip().lower()
            if "@" in v:
                raise ValueError(
                    "Domain should not include '@' symbol (e.g., use 'acme.com' not '@acme.com')"
                )
            if not v or "." not in v:
                raise ValueError("Domain must be a valid format (e.g., 'acme.com')")
        return v


# CRUD Pattern Models for Organization
class OrganizationBase(BaseModel):
    """Shared organization fields."""

    name: str = Field(max_length=255)
    domain: str | None = Field(default=None, max_length=255)
    is_active: bool = Field(default=True)
    is_provider: bool = Field(default=False)
    settings: dict = Field(default_factory=dict)


class OrganizationCreate(OrganizationBase):
    """Input for creating an organization."""

    pass


class OrganizationUpdate(BaseModel):
    """Input for updating an organization (all fields optional)."""

    name: str | None = None
    domain: str | None = None
    is_active: bool | None = None
    settings: dict | None = None


class OrganizationPublic(OrganizationBase):
    """Organization output for API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    created_by: str
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_dt(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None


class OrganizationGroupBase(BaseModel):
    """Shared organization-group fields."""

    name: str = Field(max_length=255)


class OrganizationGroupCreate(OrganizationGroupBase):
    """Input for creating an organization group."""

    member_organization_ids: list[UUID] = Field(default_factory=list)


class OrganizationGroupUpdate(BaseModel):
    """Input for updating an organization group."""

    name: str | None = None
    member_organization_ids: list[UUID] | None = None


class OrganizationGroupPublic(OrganizationGroupBase):
    """Organization-group output for API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_organization_id: UUID
    member_organization_ids: list[UUID] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_group_dt(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None
