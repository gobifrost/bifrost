"""
User, role, and permission contract models for Bifrost.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer, model_validator

if TYPE_CHECKING:
    pass


# ==================== USER MODELS ====================


class User(BaseModel):
    """User entity"""
    id: str = Field(..., description="User ID from Azure AD")
    email: str
    display_name: str
    is_superuser: bool = Field(
        default=False, description="Whether user is a platform admin (superuser)")
    organization_id: str | None = Field(
        default=None, description="Organization ID (null for system accounts)")
    is_active: bool = Field(default=True)
    last_login: datetime | None = None
    created_at: datetime

    # NEW: Entra ID fields for enhanced authentication (T007)
    entra_user_id: str | None = Field(
        None, description="Azure AD user object ID (oid claim) for duplicate prevention")
    last_entra_id_sync: datetime | None = Field(
        None, description="Last synchronization timestamp from Azure AD")


class CreateUserRequest(BaseModel):
    """Request model for creating a user"""
    email: str = Field(..., description="User email address")
    display_name: str = Field(..., min_length=1, max_length=200, description="User display name")
    is_platform_admin: bool = Field(..., description="Whether user is a platform administrator")
    org_id: str | None = Field(default=None, description="Organization ID (required if is_platform_admin=false)")

    @model_validator(mode='after')
    def validate_org_requirement(self):
        """Validate that org_id is provided for non-platform-admin users"""
        if not self.is_platform_admin and not self.org_id:
            raise ValueError("org_id is required when is_platform_admin is false")
        if self.is_platform_admin and self.org_id:
            raise ValueError("org_id must be null when is_platform_admin is true")
        return self


class UpdateUserRequest(BaseModel):
    """Request model for updating a user"""
    display_name: str | None = Field(default=None, min_length=1, max_length=200, description="User display name")
    is_active: bool | None = Field(default=None, description="Whether user is active")
    is_platform_admin: bool | None = Field(default=None, description="Whether user is a platform administrator")
    org_id: str | None = Field(default=None, description="Organization ID (required when changing to is_platform_admin=false)")

    @model_validator(mode='after')
    def validate_org_requirement(self):
        """Validate that org_id is provided when demoting to non-platform-admin"""
        if self.is_platform_admin is False and not self.org_id:
            raise ValueError("org_id is required when setting is_platform_admin to false")
        return self


# CRUD Pattern Models for User
class UserBase(BaseModel):
    """Shared user fields."""
    email: EmailStr = Field(max_length=320)
    name: str | None = Field(default=None, max_length=255)
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    is_verified: bool = Field(default=False)
    is_registered: bool = Field(default=True)
    is_system: bool = Field(default=False)
    mfa_enabled: bool = Field(default=False)


class UserCreate(BaseModel):
    """Input for creating a user."""
    email: EmailStr
    name: str | None = None
    password: str | None = None  # Plain text, will be hashed
    is_active: bool = True
    is_superuser: bool = False
    organization_id: UUID | None = None
    invite: bool = False  # If True, generate invite record; link returned and event optionally fired
    trigger_automation: bool | None = None  # None treated as True for contract compat during transition


class UserUpdate(BaseModel):
    """Input for updating a user."""
    email: EmailStr | None = None
    name: str | None = None
    password: str | None = None
    is_active: bool | None = None
    is_superuser: bool | None = None
    is_verified: bool | None = None
    mfa_enabled: bool | None = None
    organization_id: UUID | None = None


class UserPublic(UserBase):
    """User output for API responses (excludes sensitive fields)."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID | None
    last_login: datetime | None
    created_at: datetime
    updated_at: datetime
    invite_status: str = "active"  # one of InviteStatus values; populated by router

    @field_serializer("created_at", "updated_at", "last_login")
    def serialize_dt(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None


class UserResponse(BaseModel):
    """User response model."""
    id: str
    email: str
    name: str
    is_active: bool
    is_superuser: bool
    is_verified: bool


# ==================== ROLE MODELS ====================


class Role(BaseModel):
    """Role entity for organization users"""
    id: str = Field(..., description="Role ID (GUID)")
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    permissions: dict = Field(default_factory=dict)
    created_by: str
    created_at: datetime
    updated_at: datetime


class CreateRoleRequest(BaseModel):
    """Request model for creating a role"""
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    permissions: dict | None = Field(default=None)


class UpdateRoleRequest(BaseModel):
    """Request model for updating a role"""
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    permissions: dict | None = Field(default=None)


# CRUD Pattern Models for Role
class RoleBase(BaseModel):
    """Shared role fields."""
    name: str = Field(max_length=100)
    description: str | None = Field(default=None)


class RoleCreate(RoleBase):
    """Input for creating a role.

    Roles are globally defined - org scoping happens at the entity level.
    """
    permissions: dict | None = Field(default=None)


class RoleUpdate(BaseModel):
    """Input for updating a role."""
    name: str | None = None
    description: str | None = None
    permissions: dict | None = Field(default=None)


class RolePublic(RoleBase):
    """Role output for API responses.

    Roles are globally defined - org scoping happens at the entity level.
    """
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    permissions: dict = Field(default_factory=dict)
    created_by: str
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_dt(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None


class UserRole(BaseModel):
    """User-to-Role assignment entity"""
    user_id: str
    role_id: str
    assigned_by: str
    assigned_at: datetime


class FormRole(BaseModel):
    """Form-to-Role access control entity"""
    form_id: str
    role_id: str
    assigned_by: str
    assigned_at: datetime


class AssignUsersToRoleRequest(BaseModel):
    """Request model for assigning users to a role"""
    user_ids: list[str] = Field(..., min_length=1,
                               description="List of user IDs to assign")


class AssignFormsToRoleRequest(BaseModel):
    """Request model for assigning forms to a role"""
    form_ids: list[str] = Field(..., min_length=1,
                               description="List of form IDs to assign")


class RoleUsersResponse(BaseModel):
    """Response model for getting users assigned to a role"""
    user_ids: list[str] = Field(..., description="List of user IDs assigned to the role")


class RoleFormsResponse(BaseModel):
    """Response model for getting forms assigned to a role"""
    form_ids: list[str] = Field(..., description="List of form IDs assigned to the role")


# ==================== PERMISSION MODELS ====================


class UserPermission(BaseModel):
    """User permission entity"""
    user_id: str
    org_id: str
    can_execute_workflows: bool = Field(default=False)
    can_manage_config: bool = Field(default=False)
    can_manage_forms: bool = Field(default=False)
    can_view_history: bool = Field(default=False)
    granted_by: str
    granted_at: datetime


class PermissionsData(BaseModel):
    """Permissions data for grant request"""
    can_execute_workflows: bool
    can_manage_config: bool
    can_manage_forms: bool
    can_view_history: bool


class GrantPermissionsRequest(BaseModel):
    """Request model for granting permissions"""
    user_id: str
    org_id: str
    permissions: PermissionsData


class UserRolesResponse(BaseModel):
    """Response model for getting roles assigned to a user"""
    role_ids: list[str] = Field(..., description="List of role IDs assigned to the user")


class UserFormsResponse(BaseModel):
    """Response model for getting forms accessible to a user"""
    is_superuser: bool = Field(..., description="Whether user is a platform admin")
    has_access_to_all_forms: bool = Field(..., description="Whether user has access to all forms")
    form_ids: list[str] = Field(default_factory=list, description="List of form IDs user can access (empty if has_access_to_all_forms=true)")
