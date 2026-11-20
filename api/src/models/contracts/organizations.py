"""
Organization contract models for Bifrost.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

if TYPE_CHECKING:
    pass


# ==================== ORGANIZATION TAGS ====================

MAX_ORGANIZATION_TAG_LENGTH = 50
MAX_ORGANIZATION_TAGS = 50


def normalize_organization_tags(tags: list[str]) -> list[str]:
    """Trim and collapse whitespace, drop blanks, and remove case-insensitive
    duplicates while keeping the first spelling and order."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = " ".join(raw.split())
        if not tag:
            continue
        if len(tag) > MAX_ORGANIZATION_TAG_LENGTH:
            raise ValueError(
                f"Tags must be {MAX_ORGANIZATION_TAG_LENGTH} characters or fewer"
            )
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(tag)
    if len(normalized) > MAX_ORGANIZATION_TAGS:
        raise ValueError(
            f"An organization can have at most {MAX_ORGANIZATION_TAGS} tags"
        )
    return normalized


# ==================== ORGANIZATION MODELS ====================


class Organization(BaseModel):
    """Organization entity (response model)"""
    id: str = Field(..., description="Organization ID (GUID)")
    name: str = Field(..., min_length=1, max_length=200)
    domain: str | None = Field(
        None, description="Email domain for auto-provisioning users (e.g., 'acme.com')")
    is_active: bool = Field(default=True)
    is_provider: bool = Field(default=False, description="Provider organization (immutable, cannot be deleted)")
    created_at: datetime
    created_by: str
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreateOrganizationRequest(BaseModel):
    """Request model for creating an organization"""
    name: str = Field(..., min_length=1, max_length=200)
    domain: str | None = Field(
        None, description="Email domain for auto-provisioning users (e.g., 'acme.com')")

    @field_validator('domain')
    @classmethod
    def validate_domain(cls, v):
        """Validate domain format (no @ symbol, just the domain)"""
        if v is not None:
            v = v.strip().lower()
            if '@' in v:
                raise ValueError("Domain should not include '@' symbol (e.g., use 'acme.com' not '@acme.com')")
            if not v or '.' not in v:
                raise ValueError("Domain must be a valid format (e.g., 'acme.com')")
        return v


class UpdateOrganizationRequest(BaseModel):
    """Request model for updating an organization"""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    domain: str | None = Field(default=None, description="Email domain for auto-provisioning users")
    is_active: bool | None = None

    @field_validator('domain')
    @classmethod
    def validate_domain(cls, v):
        """Validate domain format (no @ symbol, just the domain)"""
        if v is not None:
            v = v.strip().lower()
            if '@' in v:
                raise ValueError("Domain should not include '@' symbol (e.g., use 'acme.com' not '@acme.com')")
            if not v or '.' not in v:
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
    tags: list[str] = Field(
        default_factory=list,
        description="Labels for grouping and filtering organizations (e.g. 'MSP')",
    )

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str]) -> list[str]:
        return normalize_organization_tags(v)


class OrganizationCreate(OrganizationBase):
    """Input for creating an organization."""
    pass


class OrganizationUpdate(BaseModel):
    """Input for updating an organization (all fields optional)."""
    name: str | None = None
    domain: str | None = None
    is_active: bool | None = None
    settings: dict | None = None
    tags: list[str] | None = Field(
        default=None,
        description="Replaces the organization's tags; omit to leave them unchanged",
    )

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else normalize_organization_tags(v)


class OrganizationTagsBulkUpdate(BaseModel):
    """Add and/or remove tags across several organizations at once."""
    organization_ids: list[UUID] = Field(..., min_length=1, max_length=5000)
    add: list[str] = Field(default_factory=list, description="Tags to add to every organization")
    remove: list[str] = Field(
        default_factory=list,
        description="Tags to remove from every organization (case-insensitive)",
    )

    @field_validator("add", "remove")
    @classmethod
    def validate_tag_lists(cls, v: list[str]) -> list[str]:
        return normalize_organization_tags(v)

    @model_validator(mode="after")
    def require_change(self) -> "OrganizationTagsBulkUpdate":
        if not self.add and not self.remove:
            raise ValueError("Provide at least one tag to add or remove")
        return self


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
