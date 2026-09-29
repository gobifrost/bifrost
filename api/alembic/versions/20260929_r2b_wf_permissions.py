"""R2b: workflow permission mode, grants, and Solution permission requests

Revision ID: 20260929_r2b_wf_permissions
Revises: 20260929_model_catalog
Create Date: 2026-09-29

Data only — nothing reads any of this until delegated execution.

- workflows.permission_mode (NULL = follow the platform default)
- workflow_permission_grants: permissions (with optional cross-org boundary)
  a restricted workflow always runs with
- solution_workflow_permission_requests: what a Solution install requests
  per workflow, and the digest an admin approved
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260929_r2b_wf_permissions"
down_revision: Union[str, None] = "20260929_model_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("workflows", sa.Column("permission_mode", sa.String(length=16), nullable=True))
    op.create_check_constraint(
        "ck_workflows_permission_mode",
        "workflows",
        "permission_mode IN ('full', 'restricted')",
    )

    op.create_table(
        "workflow_permission_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workflow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("permission", sa.String(length=100), nullable=False),
        sa.Column("boundary_kind", sa.String(length=32), nullable=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "boundary_kind IN ('organization', 'managed_organizations', 'platform')",
            name="ck_workflow_permission_grants_boundary_kind",
        ),
        sa.CheckConstraint(
            "(boundary_kind = 'organization' AND organization_id IS NOT NULL) "
            "OR (boundary_kind IS DISTINCT FROM 'organization' AND organization_id IS NULL)",
            name="ck_workflow_permission_grants_org_boundary",
        ),
        sa.UniqueConstraint(
            "workflow_id",
            "permission",
            "boundary_kind",
            "organization_id",
            name="uq_workflow_permission_grants_grant",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "ix_workflow_permission_grants_workflow_id",
        "workflow_permission_grants",
        ["workflow_id"],
    )

    op.create_table(
        "solution_workflow_permission_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "solution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("solutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requested_mode", sa.String(length=16), nullable=False),
        sa.Column(
            "requested_grants",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("approved_digest", sa.String(length=64), nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
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
            "requested_mode IN ('full', 'restricted')",
            name="ck_solution_wf_perm_requests_mode",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'declined')",
            name="ck_solution_wf_perm_requests_status",
        ),
        sa.UniqueConstraint(
            "solution_id",
            "workflow_id",
            name="uq_solution_wf_perm_requests_solution_workflow",
        ),
    )
    op.create_index(
        "ix_solution_wf_perm_requests_solution_id",
        "solution_workflow_permission_requests",
        ["solution_id"],
    )


def downgrade() -> None:
    op.drop_table("solution_workflow_permission_requests")
    op.drop_table("workflow_permission_grants")
    op.drop_constraint("ck_workflows_permission_mode", "workflows", type_="check")
    op.drop_column("workflows", "permission_mode")
