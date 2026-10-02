"""Give every role assignment made since the R2b backfill its home-org boundary

Revision ID: 20261001_r3a_boundary_fill
Revises: 20261001_graph_client_state_enc
Create Date: 2026-10-01

The R2b migration (20260929_r2b_roles) gave every existing user_roles row an
'organization' boundary at the user's home organization. Role assignments
made after it, through the role-assignment and bulk user endpoints, were
written without a boundary. From R3a every path that adds a user_roles row
writes its boundaries, so this fills in the same home-org boundary for any
row that has none.

Users with no home organization (Global users) are skipped, exactly as the
R2b backfill skipped them: there is no organization to choose, and they are
Platform Admins or the system user.

Data only and idempotent: a row that already has a boundary is left alone,
so running it again changes nothing. Code from before this revision ignores
boundary rows it does not read, so a code revert still runs on the migrated
database. Downgrade is a no-op: a filled-in boundary cannot be told apart
from one written by the application.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261001_r3a_boundary_fill"
down_revision: Union[str, None] = "20261001_graph_client_state_enc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO user_role_boundaries (id, user_id, role_id, kind, organization_id)
            SELECT gen_random_uuid(), ur.user_id, ur.role_id, 'organization', u.organization_id
            FROM user_roles ur
            JOIN users u ON u.id = ur.user_id
            WHERE u.organization_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM user_role_boundaries b
                WHERE b.user_id = ur.user_id AND b.role_id = ur.role_id
              )
            """
        )
    )


def downgrade() -> None:
    pass
