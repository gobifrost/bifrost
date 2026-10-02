"""Describe Platform Operator as the assignable role it now is

Revision ID: 20261001_r3a_operator_desc
Revises: 20261001_r3a_boundary_fill
Create Date: 2026-10-01

From R3a a Platform Admin can assign Platform Operator (as an additional
role, at managed organizations or at customer organizations), so its
description no longer says it is not assignable and now says what it does.

Data only and idempotent: the description is set, not appended to.
Downgrade restores the previous description.
"""

from __future__ import annotations

from typing import Sequence, Union
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "20261001_r3a_operator_desc"
down_revision: Union[str, None] = "20261001_r3a_boundary_fill"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PLATFORM_OPERATOR_ROLE_ID = UUID("00000000-0000-0000-0000-000000000007")
PREVIOUS_DESCRIPTION = (
    "Visibility into managed organizations, with user support and role assignment. "
    "Not yet assignable."
)
DESCRIPTION = (
    "Support for customer organizations: view organizations and users, invite users, "
    "reset MFA, deactivate ordinary users, and assign roles that carry no permissions. "
    "Additional role only."
)


def _set_description(description: str) -> None:
    op.get_bind().execute(
        sa.text("UPDATE roles SET description = :description WHERE id = CAST(:role_id AS uuid)"),
        {"description": description, "role_id": str(PLATFORM_OPERATOR_ROLE_ID)},
    )


def upgrade() -> None:
    _set_description(DESCRIPTION)


def downgrade() -> None:
    _set_description(PREVIOUS_DESCRIPTION)
