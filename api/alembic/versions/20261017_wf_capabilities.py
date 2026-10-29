"""Make workflow capabilities permission-only.

Revision ID: 20261017_wf_capabilities
Revises: 20261017_agent_actor
Create Date: 2026-10-17

Workflow capabilities are permission-only. Legacy permission rows can therefore
collapse by (workflow, permission). Legacy Solution requests are re-canonicalized
without placement, and their prior approvals are invalidated for renewed consent.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20261017_wf_capabilities"
down_revision: str | None = "20261017_agent_actor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _request_digest(mode: str, grants: list[dict[str, str]]) -> str:
    canonical = json.dumps(
        {"mode": mode, "grants": grants}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _permission_only_grants(grants: list[dict[str, object]]) -> list[dict[str, str]]:
    return [
        {"permission": permission}
        for permission in sorted({str(grant["permission"]) for grant in grants})
    ]


def upgrade() -> None:
    connection = op.get_bind()

    # Distinct legacy boundaries become the same permission-only capability.
    # Keep one row for each resulting canonical grant before narrowing
    # the database uniqueness rule.
    op.execute(
        """
        DELETE FROM workflow_permission_grants AS duplicate
        USING workflow_permission_grants AS retained
        WHERE duplicate.workflow_id = retained.workflow_id
          AND duplicate.permission = retained.permission
          AND duplicate.id > retained.id
        """
    )
    op.drop_constraint(
        "uq_workflow_permission_grants_grant", "workflow_permission_grants", type_="unique"
    )
    op.drop_constraint(
        "ck_workflow_permission_grants_boundary_kind",
        "workflow_permission_grants",
        type_="check",
    )
    op.drop_constraint(
        "ck_workflow_permission_grants_org_boundary",
        "workflow_permission_grants",
        type_="check",
    )
    op.execute(
        "ALTER TABLE workflow_permission_grants "
        "DROP CONSTRAINT IF EXISTS workflow_permission_grants_organization_id_fkey"
    )
    op.drop_column("workflow_permission_grants", "organization_id")
    op.drop_column("workflow_permission_grants", "boundary_kind")
    op.create_unique_constraint(
        "uq_workflow_permission_grants_grant",
        "workflow_permission_grants",
        ["workflow_id", "permission"],
    )

    rows = connection.execute(
        sa.text(
            """
            SELECT id, requested_mode, requested_grants
            FROM solution_workflow_permission_requests
            """
        )
    ).mappings()
    for row in rows:
        legacy_grants = row["requested_grants"]
        if not any(set(grant) - {"permission"} for grant in legacy_grants):
            continue
        grants = _permission_only_grants(legacy_grants)
        connection.execute(
            sa.text(
                """
                UPDATE solution_workflow_permission_requests
                SET requested_grants = CAST(:requested_grants AS jsonb),
                    request_digest = :request_digest,
                    status = 'pending',
                    approved_digest = NULL,
                    decided_by = NULL,
                    decided_at = NULL,
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "requested_grants": json.dumps(grants, separators=(",", ":")),
                "request_digest": _request_digest(str(row["requested_mode"]), grants),
            },
        )


def downgrade() -> None:
    op.drop_constraint(
        "uq_workflow_permission_grants_grant", "workflow_permission_grants", type_="unique"
    )
    op.add_column(
        "workflow_permission_grants",
        sa.Column("boundary_kind", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "workflow_permission_grants",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "workflow_permission_grants_organization_id_fkey",
        "workflow_permission_grants",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_workflow_permission_grants_boundary_kind",
        "workflow_permission_grants",
        "boundary_kind IN ('organization', 'managed_organizations', 'platform')",
    )
    op.create_check_constraint(
        "ck_workflow_permission_grants_org_boundary",
        "workflow_permission_grants",
        "(boundary_kind = 'organization' AND organization_id IS NOT NULL) "
        "OR (boundary_kind IS DISTINCT FROM 'organization' AND organization_id IS NULL)",
    )
    op.create_unique_constraint(
        "uq_workflow_permission_grants_grant",
        "workflow_permission_grants",
        ["workflow_id", "permission", "boundary_kind", "organization_id"],
        postgresql_nulls_not_distinct=True,
    )
