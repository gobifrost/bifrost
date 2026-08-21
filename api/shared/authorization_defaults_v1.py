"""Immutable v1 default-role definitions for the boundary authorization model.

Migrations and runtime provisioning both import this versioned module. Changes
to shipped defaults require a new versioned module and forward migration so a
historical upgrade never changes underneath an already-released revision.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


PLATFORM_ADMIN_ROLE_ID = UUID("00000000-0000-0000-0000-000000000003")
PLATFORM_OPERATOR_ROLE_ID = UUID("00000000-0000-0000-0000-000000000004")
BUILDER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000005")
PLATFORM_BUILDER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000006")
ORGANIZATION_MEMBER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000007")


@dataclass(frozen=True, slots=True)
class DefaultRole:
    id: UUID
    key: str
    name: str
    description: str
    capabilities: tuple[str, ...]
    immutable: bool
    assignable_to_resources: bool


DISCOVERY_CAPABILITIES = (
    "agents.read",
    "apps.read",
    "claims.read",
    "configs.read",
    "events.read",
    "executions.read",
    "filepolicies.read",
    "forms.read",
    "integrations.read",
    "knowledge.read",
    "managedfiles.read",
    "organizations.read",
    "policyrules.read",
    "roles.read",
    "solutions.read",
    "tabledocuments.read",
    "tables.read",
    "workflows.read",
)

ORGANIZATION_MEMBER_CAPABILITIES = tuple(
    sorted(
        {
            "agents.execute",
            "agents.read",
            "apps.read",
            "executions.read",
            "forms.read",
            "knowledge.read",
            "managedfiles.read",
            "solutions.read",
            "tabledocuments.read",
            "tables.read",
            "workflows.read",
            "workflows.execute",
        }
    )
)

BUILDER_CAPABILITIES = tuple(
    sorted(
        {
            *ORGANIZATION_MEMBER_CAPABILITIES,
            "builder.read",
            "builder.execute",
            "solutions.readwrite",
            "solutions.build.execute",
            "solutions.deploy.execute",
        }
    )
)

DIRECT_WORKSPACE_CAPABILITIES = (
    "agents.execute",
    "agents.readwrite",
    "apps.deploy.execute",
    "apps.readwrite",
    "claims.readwrite",
    "configs.readwrite",
    "events.readwrite",
    "filepolicies.readwrite",
    "forms.readwrite",
    "integrations.readwrite",
    "knowledge.readwrite",
    "managedfiles.readwrite",
    "policyrules.readwrite",
    "tabledocuments.readwrite",
    "tables.readwrite",
    "workflows.execute",
    "workflows.readwrite",
)

PLATFORM_OPERATOR_CAPABILITIES = tuple(
    sorted(
        {
            *DISCOVERY_CAPABILITIES,
            "builder.read",
            "metrics.read",
            "organizationgroups.readwrite",
            "organizations.readwrite",
            "platformjobs.execute",
            "platformjobs.read",
            "roles.readwrite",
            "solutions.publish.read",
        }
    )
)

PLATFORM_BUILDER_CAPABILITIES = tuple(
    sorted(
        {
            *BUILDER_CAPABILITIES,
            *DIRECT_WORKSPACE_CAPABILITIES,
            "organizationgroups.read",
            "repository.readwrite",
        }
    )
)

DEFAULT_ROLES_V1 = (
    DefaultRole(
        id=PLATFORM_ADMIN_ROLE_ID,
        key="platform_admin",
        name="Platform Admin",
        description="Full platform administration managed by Bifrost.",
        capabilities=("platform.superuser",),
        immutable=True,
        assignable_to_resources=False,
    ),
    DefaultRole(
        id=PLATFORM_OPERATOR_ROLE_ID,
        key="platform_operator",
        name="Platform Operator",
        description="Customer administration and support across managed organizations.",
        capabilities=PLATFORM_OPERATOR_CAPABILITIES,
        immutable=True,
        assignable_to_resources=False,
    ),
    DefaultRole(
        id=BUILDER_ROLE_ID,
        key="builder",
        name="Builder",
        description="Private-first Solution authoring without publication authority.",
        capabilities=BUILDER_CAPABILITIES,
        immutable=False,
        assignable_to_resources=True,
    ),
    DefaultRole(
        id=PLATFORM_BUILDER_ROLE_ID,
        key="platform_builder",
        name="Platform Builder",
        description="Builder plus direct managed-organization and Platform workspace authoring.",
        capabilities=PLATFORM_BUILDER_CAPABILITIES,
        immutable=False,
        assignable_to_resources=True,
    ),
    DefaultRole(
        id=ORGANIZATION_MEMBER_ROLE_ID,
        key="organization_member",
        name="Organization Member",
        description="Baseline access to discover and run resources shared with an organization member.",
        capabilities=ORGANIZATION_MEMBER_CAPABILITIES,
        immutable=False,
        assignable_to_resources=True,
    ),
)
