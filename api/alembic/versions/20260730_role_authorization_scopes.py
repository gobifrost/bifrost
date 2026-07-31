"""Add first-class role authorization scopes.

Revision ID: 20260730_role_auth_scopes
Revises: 20260727_promotion_pinning
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260730_role_auth_scopes"
down_revision: str | None = "20260727_promotion_pinning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PLATFORM_ADMIN_ROLE_ID = "00000000-0000-0000-0000-000000000003"
PLATFORM_OPERATOR_ROLE_ID = "00000000-0000-0000-0000-000000000004"


def upgrade() -> None:
    op.add_column("roles", sa.Column("key", sa.String(length=100), nullable=True))
    op.add_column(
        "roles",
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "roles",
        sa.Column(
            "is_builtin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "roles",
        sa.Column(
            "assignable_to_resources",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.create_index("uq_roles_key", "roles", ["key"], unique=True)

    insert_role = sa.text(
        """
        INSERT INTO roles (
            id, key, name, description, permissions, scopes,
            is_builtin, assignable_to_resources, created_by, created_at, updated_at
        ) VALUES (
            CAST(:id AS uuid), :key, :name, :description,
            '{}'::jsonb, CAST(:scopes AS jsonb), TRUE, FALSE,
            :created_by, NOW(), NOW()
        )
        """
    )
    op.execute(
        insert_role.bindparams(
            id=PLATFORM_ADMIN_ROLE_ID,
            key="platform_admin",
            name="Platform Admin",
            description="Full platform administration managed by Bifrost.",
            scopes='["platform.superuser"]',
            created_by="system@internal.gobifrost.com",
        )
    )
    op.execute(
        insert_role.bindparams(
            id=PLATFORM_OPERATOR_ROLE_ID,
            key="platform_operator",
            name="Platform Operator",
            description="Cross-organization operations managed by Bifrost.",
            scopes='["organization.impersonation"]',
            created_by="system@internal.gobifrost.com",
        )
    )

    # Preserve every current platform administrator through the new built-in
    # role while the stored boolean remains a compatibility projection.
    op.execute(
        sa.text(
            """
            INSERT INTO user_roles (user_id, role_id, assigned_by, assigned_at)
            SELECT id, CAST(:role_id AS uuid), :assigned_by, NOW()
            FROM users
            WHERE is_superuser IS TRUE AND is_system IS NOT TRUE
            ON CONFLICT (user_id, role_id) DO NOTHING
            """
        ).bindparams(
            role_id=PLATFORM_ADMIN_ROLE_ID,
            assigned_by="system@internal.gobifrost.com",
        )
    )

    # The Builder branch briefly stored this key in free-form permissions.
    # Move any such grants into the validated scope list and remove the second
    # source of truth.
    op.execute(
        sa.text(
            """
            UPDATE roles
            SET scopes = CASE
                    WHEN scopes ? 'solutions.build' THEN scopes
                    ELSE scopes || '["solutions.build"]'::jsonb
                END,
                permissions = permissions - 'solutions.build'
            WHERE permissions @> '{"solutions.build": true}'::jsonb
            """
        )
    )


def downgrade() -> None:
    # Preserve Builder grants if an operator must temporarily return to the
    # transitional Role.permissions implementation.
    op.execute(
        sa.text(
            """
            UPDATE roles
            SET permissions = permissions || '{"solutions.build": true}'::jsonb
            WHERE scopes ? 'solutions.build'
              AND is_builtin IS FALSE
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM roles
            WHERE id IN (CAST(:admin AS uuid), CAST(:operator AS uuid))
            """
        ).bindparams(
            admin=PLATFORM_ADMIN_ROLE_ID,
            operator=PLATFORM_OPERATOR_ROLE_ID,
        )
    )
    op.drop_index("uq_roles_key", table_name="roles")
    op.drop_column("roles", "assignable_to_resources")
    op.drop_column("roles", "is_builtin")
    op.drop_column("roles", "scopes")
    op.drop_column("roles", "key")
