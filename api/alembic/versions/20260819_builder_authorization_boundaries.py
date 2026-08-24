"""add role-assignment boundaries and canonical Builder capabilities

Revision ID: 20260819_builder_authz
Revises: 20260819_skill_file_tool

This is a forward-only Builder migration after the withdrawn revision
tombstones. It preserves existing role membership and Solution user grants,
then moves them into the final boundary-aware model.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from shared.authorization_legacy import translate_legacy_role_capabilities
from shared.authorization_defaults_v1 import (
    DEFAULT_ROLES_V1,
    ORGANIZATION_MEMBER_ROLE_ID,
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_ROLE_ID,
)


revision: str = "20260819_builder_authz"
down_revision: str = "20260819_skill_file_tool"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SYSTEM_ACTOR = "system@internal.gobifrost.com"


def _assert_unambiguous_legacy_authorization() -> None:
    """Block legacy grants whose boundary cannot be inferred safely."""

    connection = op.get_bind()
    ambiguous_roles = connection.execute(
        sa.text(
            """
            SELECT id, name
            FROM roles
            WHERE id != CAST(:operator_role_id AS uuid)
              AND scopes @> '["organization.impersonation"]'::jsonb
            ORDER BY name, id
            """
        ),
        {"operator_role_id": str(PLATFORM_OPERATOR_ROLE_ID)},
    ).mappings()
    ambiguous = [f"{role['name']} ({role['id']})" for role in ambiguous_roles]
    if ambiguous:
        raise RuntimeError(
            "Builder authorization migration cannot infer boundaries for custom "
            "Roles containing organization.impersonation. Remove or replace that "
            "legacy grant before upgrading. Ambiguous Roles: " + ", ".join(ambiguous)
        )

    boundaryless_assignments = connection.execute(
        sa.text(
            """
            SELECT u.email, r.name
            FROM user_roles AS assignment
            JOIN users AS u ON u.id = assignment.user_id
            JOIN roles AS r ON r.id = assignment.role_id
            WHERE u.is_system IS NOT TRUE
              AND u.organization_id IS NULL
              AND r.id != CAST(:admin_role_id AS uuid)
            ORDER BY u.email, r.name
            """
        ),
        {"admin_role_id": str(PLATFORM_ADMIN_ROLE_ID)},
    ).mappings()
    boundaryless = [
        f"{assignment['email']} -> {assignment['name']}"
        for assignment in boundaryless_assignments
    ]
    if boundaryless:
        raise RuntimeError(
            "Builder authorization migration found non-system users with no home "
            "organization and non-admin Role grants. Assign a home organization or "
            "remove the grant before upgrading. Ambiguous assignments: "
            + ", ".join(boundaryless)
        )


def _migrate_roles() -> None:
    op.alter_column("roles", "scopes", new_column_name="capabilities")
    connection = op.get_bind()
    roles = connection.execute(
        sa.text("SELECT id, capabilities, permissions FROM roles")
    ).mappings()
    for role in roles:
        connection.execute(
            sa.text(
                "UPDATE roles SET capabilities = CAST(:capabilities AS jsonb) "
                "WHERE id = :role_id"
            ),
            {
                "role_id": role["id"],
                "capabilities": json.dumps(
                    translate_legacy_role_capabilities(
                        role["capabilities"],
                        role["permissions"],
                    )
                ),
            },
        )
    upsert = sa.text(
        """
        INSERT INTO roles (
            id, key, name, description, capabilities, is_builtin,
            assignable_to_resources, created_by, created_at, updated_at
        ) VALUES (
            CAST(:id AS uuid), :key, :name, :description,
            CAST(:capabilities AS jsonb), :is_builtin,
            :assignable_to_resources, :created_by, NOW(), NOW()
        )
        ON CONFLICT (id) DO UPDATE
        SET key = EXCLUDED.key,
            name = EXCLUDED.name,
            description = EXCLUDED.description,
            capabilities = EXCLUDED.capabilities,
            is_builtin = EXCLUDED.is_builtin,
            assignable_to_resources = EXCLUDED.assignable_to_resources,
            updated_at = NOW()
        """
    )
    for role in DEFAULT_ROLES_V1:
        connection.execute(
            upsert,
            {
                "id": str(role.id),
                "key": role.key,
                "name": role.name,
                "description": role.description,
                "capabilities": json.dumps(role.capabilities),
                "is_builtin": role.immutable,
                "assignable_to_resources": role.assignable_to_resources,
                "created_by": SYSTEM_ACTOR,
            },
        )


def _migrate_role_assignments() -> None:
    op.rename_table("user_roles", "role_assignments")
    op.execute(
        "ALTER TABLE role_assignments RENAME CONSTRAINT "
        "user_roles_user_id_fkey TO role_assignments_user_id_fkey"
    )
    op.execute(
        "ALTER TABLE role_assignments RENAME CONSTRAINT "
        "user_roles_role_id_fkey TO role_assignments_role_id_fkey"
    )
    op.add_column(
        "role_assignments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )
    op.add_column(
        "role_assignments",
        sa.Column(
            "assigned_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE role_assignments AS assignment
        SET assigned_by_user_id = actor.id
        FROM users AS actor
        WHERE lower(actor.email) = lower(assignment.assigned_by)
        """
    )
    op.drop_constraint("user_roles_pkey", "role_assignments", type_="primary")
    op.alter_column("role_assignments", "id", nullable=False)
    op.create_primary_key("pk_role_assignments", "role_assignments", ["id"])
    op.create_unique_constraint(
        "uq_role_assignments_user_role",
        "role_assignments",
        ["user_id", "role_id"],
    )
    op.create_foreign_key(
        "fk_role_assignments_assigned_by_user_id",
        "role_assignments",
        "users",
        ["assigned_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_role_assignments_user", "role_assignments", ["user_id"])
    op.create_index("ix_role_assignments_role", "role_assignments", ["role_id"])
    op.drop_column("role_assignments", "assigned_by")

    op.create_table(
        "role_assignment_boundaries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "role_assignment_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("boundary_kind", sa.String(length=32), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "organization_group_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "boundary_kind IN ('organization', 'organization_group', "
            "'managed_organizations', 'platform')",
            name="ck_role_assignment_boundaries_kind",
        ),
        sa.CheckConstraint(
            "(boundary_kind = 'organization' AND organization_id IS NOT NULL "
            "AND organization_group_id IS NULL) OR "
            "(boundary_kind = 'organization_group' AND organization_group_id "
            "IS NOT NULL AND organization_id IS NULL) OR "
            "(boundary_kind IN ('managed_organizations', 'platform') AND "
            "organization_id IS NULL AND organization_group_id IS NULL)",
            name="ck_role_assignment_boundaries_shape",
        ),
        sa.ForeignKeyConstraint(
            ["role_assignment_id"],
            ["role_assignments.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_group_id"],
            ["organization_groups.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_role_assignment_boundaries_assignment",
        "role_assignment_boundaries",
        ["role_assignment_id"],
    )
    op.create_index(
        "uq_role_assignment_boundaries_organization",
        "role_assignment_boundaries",
        ["role_assignment_id", "organization_id"],
        unique=True,
        postgresql_where=sa.text("boundary_kind = 'organization'"),
    )
    op.create_index(
        "uq_role_assignment_boundaries_organization_group",
        "role_assignment_boundaries",
        ["role_assignment_id", "organization_group_id"],
        unique=True,
        postgresql_where=sa.text("boundary_kind = 'organization_group'"),
    )
    for kind in ("managed_organizations", "platform"):
        op.create_index(
            f"uq_role_assignment_boundaries_{kind}",
            "role_assignment_boundaries",
            ["role_assignment_id"],
            unique=True,
            postgresql_where=sa.text(f"boundary_kind = '{kind}'"),
        )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            INSERT INTO role_assignments (
                id, user_id, role_id, assigned_by_user_id, assigned_at
            )
            SELECT gen_random_uuid(), u.id, CAST(:role_id AS uuid), NULL, NOW()
            FROM users AS u
            WHERE u.is_system IS NOT TRUE
              AND u.organization_id IS NOT NULL
            ON CONFLICT (user_id, role_id) DO NOTHING
            """
        ),
        {"role_id": str(ORGANIZATION_MEMBER_ROLE_ID)},
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO role_assignments (
                id, user_id, role_id, assigned_by_user_id, assigned_at
            )
            SELECT
                gen_random_uuid(), member.id,
                CAST(:role_id AS uuid), NULL, NOW()
            FROM users AS member
            WHERE member.is_system IS NOT TRUE
              AND member.is_superuser IS TRUE
            ON CONFLICT (user_id, role_id) DO NOTHING
            """
        ),
        {"role_id": str(PLATFORM_ADMIN_ROLE_ID)},
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO role_assignments (
                id, user_id, role_id, assigned_by_user_id, assigned_at
            )
            SELECT
                gen_random_uuid(), member.id,
                CAST(:role_id AS uuid), NULL, NOW()
            FROM users AS member
            JOIN organizations AS organization
              ON organization.id = member.organization_id
            WHERE member.is_system IS NOT TRUE
              AND member.is_superuser IS NOT TRUE
              AND organization.is_provider IS TRUE
            ON CONFLICT (user_id, role_id) DO NOTHING
            """
        ),
        {"role_id": str(PLATFORM_OPERATOR_ROLE_ID)},
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO role_assignment_boundaries (
                id, role_assignment_id, boundary_kind, organization_id
            )
            SELECT
                gen_random_uuid(), assignment.id,
                CASE
                    WHEN assignment.role_id = CAST(:admin_role_id AS uuid)
                        THEN 'platform'
                    WHEN assignment.role_id = CAST(:operator_role_id AS uuid)
                        THEN 'managed_organizations'
                    WHEN member.organization_id IS NULL THEN 'platform'
                    ELSE 'organization'
                END,
                CASE
                    WHEN assignment.role_id IN (
                        CAST(:admin_role_id AS uuid),
                        CAST(:operator_role_id AS uuid)
                    ) OR member.organization_id IS NULL THEN NULL
                    ELSE member.organization_id
                END
            FROM role_assignments AS assignment
            JOIN users AS member ON member.id = assignment.user_id
            ON CONFLICT DO NOTHING
            """
        ),
        {
            "admin_role_id": str(PLATFORM_ADMIN_ROLE_ID),
            "operator_role_id": str(PLATFORM_OPERATOR_ROLE_ID),
        },
    )


def _create_organization_groups() -> None:
    op.create_table(
        "organization_groups",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "owner_organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_organization_id",
            "name",
            name="uq_organization_groups_owner_name",
        ),
    )
    op.create_index(
        "ix_organization_groups_owner",
        "organization_groups",
        ["owner_organization_id"],
    )
    op.create_table(
        "organization_group_members",
        sa.Column(
            "organization_group_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_group_id"],
            ["organization_groups.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("organization_group_id", "organization_id"),
    )
    op.create_index(
        "ix_organization_group_members_organization",
        "organization_group_members",
        ["organization_id"],
    )


def _migrate_solution_grants() -> None:
    op.rename_table("solution_builder_collaborators", "solution_user_grants")
    op.execute(
        "ALTER TABLE solution_user_grants RENAME CONSTRAINT "
        "solution_builder_collaborators_pkey TO solution_user_grants_pkey"
    )
    op.execute(
        "ALTER TABLE solution_user_grants RENAME CONSTRAINT "
        "uq_solution_builder_collaborator_user TO "
        "uq_solution_user_grants_solution_user"
    )
    op.execute(
        "ALTER TABLE solution_user_grants RENAME CONSTRAINT "
        "ck_solution_builder_collaborators_access TO ck_solution_user_grants_access"
    )
    op.execute(
        "ALTER INDEX ix_solution_builder_collaborators_solution_id "
        "RENAME TO ix_solution_user_grants_solution_id"
    )
    op.execute(
        "ALTER INDEX ix_solution_builder_collaborators_user "
        "RENAME TO ix_solution_user_grants_user"
    )
    op.alter_column(
        "solution_user_grants",
        "invited_by",
        new_column_name="granted_by_user_id",
    )
    op.create_table(
        "solution_role_grants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("solution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "access", sa.String(length=16), nullable=False, server_default="edit"
        ),
        sa.Column(
            "granted_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "access IN ('view', 'edit')",
            name="ck_solution_role_grants_access",
        ),
        sa.ForeignKeyConstraint(["solution_id"], ["solutions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "solution_id",
            "role_id",
            name="uq_solution_role_grants_solution_role",
        ),
    )
    op.create_index(
        "ix_solution_role_grants_solution",
        "solution_role_grants",
        ["solution_id"],
    )
    op.create_index(
        "ix_solution_role_grants_role",
        "solution_role_grants",
        ["role_id"],
    )


def upgrade() -> None:
    _assert_unambiguous_legacy_authorization()
    _migrate_roles()
    _create_organization_groups()
    _migrate_role_assignments()
    _migrate_solution_grants()


def downgrade() -> None:
    # This authorization cutover is intentionally forward-only.
    pass
