"""Contracts for role assignments, role permission sets, and the caller's own
authorization summary (R3a).

A role assignment is a base role (exactly one per user) plus any number of
additional roles, each applying at one or more boundaries. See
``src.services.user_role_assignments`` for the rules, and
``src.services.authorization`` for how boundaries are decided.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

BoundaryKindLiteral = Literal["organization", "managed_organizations", "platform"]


class RoleBoundaryInput(BaseModel):
    """Where an additional role applies."""

    kind: BoundaryKindLiteral
    organization_id: UUID | None = Field(
        default=None,
        description="Required for kind='organization'; must be unset otherwise.",
    )

    @model_validator(mode="after")
    def _organization_matches_kind(self) -> "RoleBoundaryInput":
        if (self.kind == "organization") != (self.organization_id is not None):
            raise ValueError("organization_id is required for kind='organization' and only for it")
        return self


class RoleBoundaryPublic(BaseModel):
    kind: BoundaryKindLiteral
    organization_id: UUID | None = None
    organization_name: str | None = None


class RoleSummary(BaseModel):
    id: UUID
    name: str
    is_builtin: bool


class AssignedRole(BaseModel):
    role_id: UUID
    name: str
    is_builtin: bool
    permissions: list[str]
    boundaries: list[RoleBoundaryPublic]


class AssignableRole(BaseModel):
    """A role the caller may grant to this user (the server applies the
    grant ceiling)."""

    id: UUID
    name: str
    is_builtin: bool
    permissions: list[str]
    can_be_base: bool = Field(
        description="Whether the caller may make this the user's base role."
    )
    can_be_additional: bool = Field(
        description="Whether the caller may add this as an additional role."
    )


class UserRoleAssignmentsResponse(BaseModel):
    base_role: RoleSummary
    additional: list[AssignedRole]
    is_protected: bool = Field(
        description=(
            "The user holds privileged access somewhere, so only a Platform "
            "Admin can change them."
        )
    )
    assignable_roles: list[AssignableRole]


class AdditionalRoleInput(BaseModel):
    role_id: UUID
    boundaries: list[RoleBoundaryInput] | None = Field(
        default=None,
        description=(
            "Where the role applies. Omit for the default: the user's home "
            "organization (Platform for a Global user)."
        ),
    )


class UserRoleAssignmentsUpdate(BaseModel):
    """Replaces a user's base role and additional roles atomically."""

    base_role_id: UUID
    additional: list[AdditionalRoleInput] = Field(default_factory=list)


class RolePermissionItem(BaseModel):
    permission: str
    editable: bool = Field(description="Whether PUT /api/roles/{role_id}/permissions may change it.")
    privileged: bool = Field(description="Holders of this permission become protected users.")


class RolePermissionsResponse(BaseModel):
    role_id: UUID
    is_builtin: bool
    permissions: list[RolePermissionItem] = Field(description="Every permission the role holds.")
    identity_permissions: list[RolePermissionItem] = Field(
        description="The identity permissions an editor may choose from, held or not."
    )


class RolePermissionsUpdate(BaseModel):
    """The role's identity permissions (users, users.lifecycle, organizations,
    roleassignments, roles). Other permissions the role holds are kept."""

    permissions: list[str]


class AuthorizationBoundary(BaseModel):
    kind: Literal["home", "organization", "managed_organizations", "platform"]
    organization_id: UUID | None = None


class AuthorizationGrant(BaseModel):
    permission: str
    boundary: AuthorizationBoundary


class AuthorizationBaseRole(BaseModel):
    id: UUID
    name: str


class AuthorizationSummary(BaseModel):
    """What the signed-in user holds, for the UI to decide which controls to
    show. The server decides every request on its own."""

    is_platform_admin: bool
    home_organization_id: UUID | None
    provider_organization_id: UUID
    base_role: AuthorizationBaseRole
    grants: list[AuthorizationGrant] = Field(
        description=(
            "Base-role permissions (kind='home', the home organization only) "
            "and every additional role's permissions at each of its "
            "boundaries. Empty for a Platform Admin, who holds everything "
            "except secrets.read."
        )
    )
