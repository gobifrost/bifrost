"""
User, role, and permission contract models for Bifrost.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

if TYPE_CHECKING:
    pass


# ==================== USER MODELS ====================


class User(BaseModel):
    """User entity"""

    id: str = Field(..., description="User ID from Azure AD")
    email: str
    display_name: str
    is_superuser: bool = Field(
        default=False, description="Whether user is a platform admin (superuser)"
    )
    organization_id: str | None = Field(
        default=None, description="Organization ID (null for system accounts)"
    )
    is_active: bool = Field(default=True)
    last_login: datetime | None = None
    created_at: datetime

    # NEW: Entra ID fields for enhanced authentication (T007)
    entra_user_id: str | None = Field(
        None, description="Azure AD user object ID (oid claim) for duplicate prevention"
    )
    last_entra_id_sync: datetime | None = Field(
        None, description="Last synchronization timestamp from Azure AD"
    )


class CreateUserRequest(BaseModel):
    """Request model for creating a user"""

    email: str = Field(..., description="User email address")
    display_name: str = Field(
        ..., min_length=1, max_length=200, description="User display name"
    )
    is_platform_admin: bool = Field(
        ..., description="Whether user is a platform administrator"
    )
    org_id: str | None = Field(
        default=None,
        description="Organization ID (required if is_platform_admin=false)",
    )

    @model_validator(mode="after")
    def validate_org_requirement(self):
        """Validate that org_id is provided for non-platform-admin users"""
        if not self.is_platform_admin and not self.org_id:
            raise ValueError("org_id is required when is_platform_admin is false")
        if self.is_platform_admin and self.org_id:
            raise ValueError("org_id must be null when is_platform_admin is true")
        return self


class UpdateUserRequest(BaseModel):
    """Request model for updating a user"""

    display_name: str | None = Field(
        default=None, min_length=1, max_length=200, description="User display name"
    )
    is_active: bool | None = Field(default=None, description="Whether user is active")
    is_platform_admin: bool | None = Field(
        default=None, description="Whether user is a platform administrator"
    )
    org_id: str | None = Field(
        default=None,
        description="Organization ID (required when changing to is_platform_admin=false)",
    )

    @model_validator(mode="after")
    def validate_org_requirement(self):
        """Validate that org_id is provided when demoting to non-platform-admin"""
        if self.is_platform_admin is False and not self.org_id:
            raise ValueError(
                "org_id is required when setting is_platform_admin to false"
            )
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
    is_external: bool = Field(
        default=False,
        description=(
            "External (portal/guest) user: sees only their own org tier — no "
            "global entities and no 'authenticated' access-level entitlement"
        ),
    )
    mfa_enabled: bool = Field(default=False)


class UserCreate(BaseModel):
    """Input for creating a user."""

    email: EmailStr
    name: str | None = None
    password: str | None = None  # Plain text, will be hashed
    is_active: bool = True
    is_superuser: bool = False
    is_external: bool = False  # External (portal/guest) user — settable at invite
    organization_id: UUID | None = None
    invite: bool = False  # If True, generate invite record; link returned and event optionally fired
    trigger_automation: bool | None = (
        None  # None treated as True for contract compat during transition
    )


class UserUpdate(BaseModel):
    """Input for updating a user."""

    email: EmailStr | None = None
    name: str | None = None
    password: str | None = None
    is_active: bool | None = None
    is_superuser: bool | None = None
    is_verified: bool | None = None
    is_external: bool | None = None
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
    registration_url: str | None = (
        None  # only populated immediately after invite creation
    )

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


# ==================== BULK USER OPERATIONS ====================


class BulkUserOperation(BaseModel):
    """One bulk operation on a set of users.

    Exactly one of `organization_id`, `role_assignments`, or `is_active` is required;
    `operation` identifies which.
    """

    user_ids: list[UUID] = Field(..., min_length=1, max_length=500)
    operation: str = Field(
        ..., description="One of: move_org, replace_roles, set_active"
    )
    organization_id: UUID | None = Field(
        default=None,
        description="Target org for move_org. None means move to platform/provider org.",
    )
    role_assignments: list["RoleAssignmentSelection"] | None = Field(
        default=None,
        description="Full boundary-aware role set for replace_roles. Empty clears all roles.",
    )
    role_ids: list[UUID] | None = Field(
        default=None,
        description=(
            "Legacy replace_roles input. When supplied without role_assignments, "
            "the route infers the requester's home-organization boundary."
        ),
    )
    is_active: bool | None = Field(
        default=None, description="Target active state for set_active."
    )

    @model_validator(mode="after")
    def validate_operation(self):
        if self.operation == "move_org":
            # organization_id may be None (= platform)
            pass
        elif self.operation == "replace_roles":
            if self.role_assignments is not None and self.role_ids is not None:
                assignment_role_ids = {
                    selection.role_id for selection in self.role_assignments
                }
                if assignment_role_ids != set(self.role_ids):
                    raise ValueError(
                        "Use either role_assignments or matching legacy role_ids, not both"
                    )
            if self.role_assignments is None and self.role_ids is None:
                raise ValueError(
                    "role_assignments or legacy role_ids is required for replace_roles"
                )
        elif self.operation == "set_active":
            if self.is_active is None:
                raise ValueError("is_active is required for set_active")
        else:
            raise ValueError(
                f"Unknown operation '{self.operation}'. Must be move_org, replace_roles, or set_active."
            )
        return self


class BulkUserFailure(BaseModel):
    """A single user the bulk op couldn't apply to."""

    user_id: UUID
    reason: str


class BulkUserResponse(BaseModel):
    """Result of a bulk user operation."""

    succeeded: list[UUID]
    failed: list[BulkUserFailure]


# ==================== ROLE MODELS ====================


# CRUD Pattern Models for Role
def _normalize_legacy_role_capability_fields(data: Any) -> Any:
    """Accept pre-capability Role inputs without making them canonical output.

    ``scopes`` was the transitional name for the same list of role capability
    keys. ``permissions`` remains deprecated compatibility metadata, but the
    known ``can_promote_agent`` flag also contributes its migrated capability.
    """

    if not isinstance(data, dict):
        return data
    normalized = dict(data)
    legacy_scopes = normalized.pop("scopes", None)
    legacy_permissions = normalized.get("permissions")
    capabilities = normalized.get("capabilities")

    if legacy_scopes is not None:
        from shared.authorization_legacy import translate_legacy_role_capabilities

        translated_scopes = translate_legacy_role_capabilities(legacy_scopes, None)
        if capabilities is not None and list(capabilities) != translated_scopes:
            raise ValueError("Use either capabilities or matching legacy scopes, not both")
        normalized["capabilities"] = translated_scopes
        capabilities = translated_scopes

    if legacy_permissions not in (None, {}, []):
        from shared.authorization_legacy import translate_legacy_role_capabilities

        translated_permissions = translate_legacy_role_capabilities(
            None,
            legacy_permissions,
        )
        normalized["capabilities"] = sorted(
            {*(normalized.get("capabilities") or []), *translated_permissions}
        )

    return normalized


class RoleBase(BaseModel):
    """Shared role fields."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None)


class RoleCreate(RoleBase):
    """Input for creating a role.

    Roles are globally defined - org scoping happens at the entity level.
    """

    capabilities: list[str] = Field(default_factory=list)
    scopes: list[str] | None = Field(
        default=None,
        description="Deprecated alias for capabilities; accepted for compatibility.",
    )
    permissions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, data: Any) -> Any:
        return _normalize_legacy_role_capability_fields(data)

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, capabilities: list[str]) -> list[str]:
        from shared.authorization_scopes import validate_role_scopes

        return validate_role_scopes(capabilities, custom_role=True)


class RoleUpdate(BaseModel):
    """Input for updating a role."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    capabilities: list[str] | None = Field(default=None)
    scopes: list[str] | None = Field(
        default=None,
        description="Deprecated alias for capabilities; accepted for compatibility.",
    )
    permissions: dict[str, Any] | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, data: Any) -> Any:
        return _normalize_legacy_role_capability_fields(data)

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, capabilities: list[str] | None) -> list[str] | None:
        if capabilities is None:
            return None
        from shared.authorization_scopes import validate_role_scopes

        return validate_role_scopes(capabilities, custom_role=True)


class RolePublic(RoleBase):
    """Role output for API responses.

    Roles are globally defined - org scoping happens at the entity level.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    permissions: dict[str, Any] = Field(default_factory=dict)
    is_builtin: bool = False
    assignable_to_resources: bool = True
    created_by: str
    created_at: datetime
    updated_at: datetime
    consumer_counts: "RoleConsumerCounts | None" = Field(
        default=None,
        description=(
            "Inline counts of every consumer type. Populated on list-roles for the "
            "Roles UI; may be None on single-role responses where it's not needed."
        ),
    )

    @model_validator(mode="after")
    def populate_deprecated_scopes(self) -> "RolePublic":
        self.scopes = list(self.capabilities)
        return self

    @field_serializer("created_at", "updated_at")
    def serialize_dt(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None


class AuthorizationCapabilityPublic(BaseModel):
    """Display metadata for one code-owned authorization capability."""

    key: str
    display_name: str
    description: str
    category: str
    is_privileged: bool
    assignable_to_custom_roles: bool


class AuthorizationTargetPublic(BaseModel):
    """One executable or collection authorization context for the caller."""

    boundary: str
    kind: Literal["organization", "managed_organizations", "platform"]
    label: str
    capabilities: list[str] = Field(default_factory=list)
    organization_id: UUID | None = None
    is_provider: bool = False


class AuthorizationTargetsPublic(BaseModel):
    """All request contexts backed by the caller's current Role assignments."""

    targets: list[AuthorizationTargetPublic] = Field(default_factory=list)


class RoleAssignmentBoundaryBase(BaseModel):
    """One boundary selection attached to a role assignment."""

    boundary_kind: Literal[
        "organization",
        "organization_group",
        "managed_organizations",
        "platform",
    ]
    organization_id: UUID | None = None
    organization_group_id: UUID | None = None

    def identity(self) -> tuple[str, UUID | None, UUID | None]:
        return (self.boundary_kind, self.organization_id, self.organization_group_id)

    @model_validator(mode="after")
    def validate_boundary_shape(self) -> "RoleAssignmentBoundaryBase":
        if self.boundary_kind == "organization":
            if self.organization_id is None or self.organization_group_id is not None:
                raise ValueError(
                    "organization boundaries require organization_id and no organization_group_id"
                )
        elif self.boundary_kind == "organization_group":
            if self.organization_group_id is None or self.organization_id is not None:
                raise ValueError(
                    "organization_group boundaries require organization_group_id and no organization_id"
                )
        else:
            if (
                self.organization_id is not None
                or self.organization_group_id is not None
            ):
                raise ValueError(
                    f"{self.boundary_kind} boundaries do not take organization identifiers"
                )
        return self


class RoleAssignmentBoundaryInput(RoleAssignmentBoundaryBase):
    """Request-body shape for one role-assignment boundary."""

    pass


class RoleAssignmentBoundaryPublic(RoleAssignmentBoundaryBase):
    """Persisted role-assignment boundary."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID


class RoleAssignmentPublic(BaseModel):
    """Durable role assignment with explicit boundary selections."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    role_id: UUID
    assigned_by_user_id: UUID | None = None
    assigned_at: datetime
    boundaries: list[RoleAssignmentBoundaryPublic] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_boundaries(self) -> "RoleAssignmentPublic":
        if not self.boundaries:
            raise ValueError("role assignments require at least one boundary selection")
        identities = [boundary.identity() for boundary in self.boundaries]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate boundary selections are not allowed")
        return self


class RoleAssignmentCreate(BaseModel):
    """Request body for creating a role assignment."""

    user_id: UUID
    role_id: UUID
    boundaries: list[RoleAssignmentBoundaryInput] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_boundaries(self) -> "RoleAssignmentCreate":
        identities = [boundary.identity() for boundary in self.boundaries]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate boundary selections are not allowed")
        return self


class RoleAssignmentSelection(BaseModel):
    """A Role and its boundaries when replacing a user's assignment set."""

    role_id: UUID
    boundaries: list[RoleAssignmentBoundaryInput] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_boundaries(self) -> "RoleAssignmentSelection":
        identities = [boundary.identity() for boundary in self.boundaries]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate boundary selections are not allowed")
        return self


class FormRole(BaseModel):
    """Form-to-Role access control entity"""

    form_id: str
    role_id: str
    assigned_by: str
    assigned_at: datetime


class AssignUsersToRoleRequest(BaseModel):
    """Request model for assigning users to a role"""

    user_ids: list[str] = Field(
        ..., min_length=1, description="List of user IDs to assign"
    )
    boundaries: list[RoleAssignmentBoundaryInput] | None = Field(default=None)


class AssignFormsToRoleRequest(BaseModel):
    """Request model for assigning forms to a role"""

    form_ids: list[str] = Field(
        ..., min_length=1, description="List of form IDs to assign"
    )


class UnassignUsersFromRoleRequest(BaseModel):
    """Request body for bulk unassigning users from a role."""

    user_ids: list[str] = Field(..., min_length=1, max_length=500)


class UnassignFormsFromRoleRequest(BaseModel):
    """Request body for bulk unassigning forms from a role."""

    form_ids: list[str] = Field(..., min_length=1, max_length=500)


class UnassignAgentsFromRoleRequest(BaseModel):
    """Request body for bulk unassigning agents from a role."""

    agent_ids: list[str] = Field(..., min_length=1, max_length=500)


class AssignAppsToRoleRequest(BaseModel):
    """Request body for bulk assigning apps to a role."""

    app_ids: list[str] = Field(..., min_length=1, max_length=500)


class UnassignAppsFromRoleRequest(BaseModel):
    """Request body for bulk unassigning apps from a role."""

    app_ids: list[str] = Field(..., min_length=1, max_length=500)


class AssignWorkflowsToRoleRequest(BaseModel):
    """Request body for bulk assigning workflows to a role."""

    workflow_ids: list[str] = Field(..., min_length=1, max_length=500)


class UnassignWorkflowsFromRoleRequest(BaseModel):
    """Request body for bulk unassigning workflows from a role."""

    workflow_ids: list[str] = Field(..., min_length=1, max_length=500)


class RoleUsersResponse(BaseModel):
    """Response model for getting users assigned to a role"""

    user_ids: list[str] = Field(
        ..., description="List of user IDs assigned to the role"
    )


class RoleFormsResponse(BaseModel):
    """Response model for getting forms assigned to a role"""

    form_ids: list[str] = Field(
        ..., description="List of form IDs assigned to the role"
    )


class RoleAppsResponse(BaseModel):
    """Response model for getting apps assigned to a role."""

    app_ids: list[str] = Field(..., description="App IDs assigned to the role")


class RoleWorkflowsResponse(BaseModel):
    """Response model for getting workflows assigned to a role."""

    workflow_ids: list[str] = Field(
        ..., description="Workflow IDs assigned to the role"
    )


class RoleKnowledgeEntry(BaseModel):
    """A single knowledge-namespace assignment under a role."""

    id: UUID
    namespace: str
    organization_id: UUID | None = None


class RoleKnowledgeResponse(BaseModel):
    """Response model for getting knowledge namespaces assigned to a role."""

    entries: list[RoleKnowledgeEntry] = Field(default_factory=list)


class KnowledgeAssignmentInput(BaseModel):
    """One namespace+org pair to assign to a role."""

    namespace: str = Field(..., min_length=1, max_length=255)
    organization_id: UUID | None = None


class AssignKnowledgeToRoleRequest(BaseModel):
    """Request body for bulk assigning knowledge namespaces to a role."""

    entries: list[KnowledgeAssignmentInput] = Field(..., min_length=1, max_length=500)


class UnassignKnowledgeFromRoleRequest(BaseModel):
    """Request body for bulk unassigning knowledge namespaces from a role."""

    assignment_ids: list[UUID] = Field(..., min_length=1, max_length=500)


class RoleConsumerCounts(BaseModel):
    """Inline counts of every consumer type for a role."""

    users: int = 0
    forms: int = 0
    agents: int = 0
    apps: int = 0
    workflows: int = 0
    knowledge: int = 0


class UserRolesResponse(BaseModel):
    """Response model for getting roles assigned to a user"""

    role_ids: list[str] = Field(
        ..., description="List of role IDs assigned to the user"
    )


class UserFormsResponse(BaseModel):
    """Response model for getting forms accessible to a user"""

    is_superuser: bool = Field(..., description="Whether user is a platform admin")
    has_access_to_all_forms: bool = Field(
        ..., description="Whether user has access to all forms"
    )
    form_ids: list[str] = Field(
        default_factory=list,
        description="List of form IDs user can access (empty if has_access_to_all_forms=true)",
    )
