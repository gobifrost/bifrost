"""Add private Builder collaboration and shadow Platform Operator grants.

Revision ID: 20260807_builder_collaboration
Revises: 20260807_complete_builder
Create Date: 2026-08-07

This is intentionally forward-only after the withdrawn Builder tombstones.
It does not restore or alter any withdrawn revision body.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260807_builder_collaboration"
down_revision: str = "20260807_complete_builder"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLATFORM_OPERATOR_ROLE_ID = "00000000-0000-0000-0000-000000000004"
PROVIDER_ORG_ID = "00000000-0000-0000-0000-000000000002"
SYSTEM_ACTOR = "system@internal.gobifrost.com"


def upgrade() -> None:
    op.create_table(
        "solution_builder_collaborators",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("solution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "access",
            sa.String(length=16),
            nullable=False,
            server_default="edit",
        ),
        sa.Column("invited_by", postgresql.UUID(as_uuid=True), nullable=True),
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
            name="ck_solution_builder_collaborators_access",
        ),
        sa.ForeignKeyConstraint(
            ["solution_id"],
            ["solutions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "solution_id",
            "user_id",
            name="uq_solution_builder_collaborator_user",
        ),
    )
    op.create_index(
        "ix_solution_builder_collaborators_solution_id",
        "solution_builder_collaborators",
        ["solution_id"],
    )
    op.create_index(
        "ix_solution_builder_collaborators_user",
        "solution_builder_collaborators",
        ["user_id"],
    )

    # Compatibility shadow assignment: provider-organization members retain
    # today's provider behavior while receiving the role that will eventually
    # replace those legacy provider-org checks. Platform admins keep their
    # separate immutable role and do not need a redundant operator assignment.
    op.execute(
        sa.text(
            """
            INSERT INTO user_roles (user_id, role_id, assigned_by, assigned_at)
            SELECT u.id, CAST(:role_id AS uuid), :assigned_by, NOW()
            FROM users AS u
            WHERE u.organization_id = CAST(:provider_org_id AS uuid)
              AND u.is_superuser IS NOT TRUE
              AND u.is_system IS NOT TRUE
            ON CONFLICT (user_id, role_id) DO NOTHING
            """
        ).bindparams(
            role_id=PLATFORM_OPERATOR_ROLE_ID,
            provider_org_id=PROVIDER_ORG_ID,
            assigned_by=SYSTEM_ACTOR,
        )
    )


def downgrade() -> None:
    # Role assignments intentionally remain sticky. Removing the collaboration
    # table is safe because it only removes explicit grants, not Solution data.
    op.drop_index(
        "ix_solution_builder_collaborators_user",
        table_name="solution_builder_collaborators",
    )
    op.drop_index(
        "ix_solution_builder_collaborators_solution_id",
        table_name="solution_builder_collaborators",
    )
    op.drop_table("solution_builder_collaborators")
