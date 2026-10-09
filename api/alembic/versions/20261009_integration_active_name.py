"""Permit a clean shell when an integration name was soft-deleted.

Revision ID: 20261009_integration_active_name
Revises: 20261008_webhook_raw_body
Create Date: 2026-10-09
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261009_integration_active_name"
down_revision: Union[str, None] = "20261008_webhook_raw_body"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("integrations_name_key", "integrations", type_="unique")
    op.create_index(
        "uq_integrations_active_name",
        "integrations",
        ["name"],
        unique=True,
        postgresql_where=sa.text("is_deleted IS FALSE"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    duplicate_name = bind.execute(
        sa.text(
            "SELECT 1 FROM integrations GROUP BY name HAVING count(*) > 1 LIMIT 1"
        )
    ).scalar_one_or_none()
    if duplicate_name is not None:
        raise RuntimeError(
            "Cannot restore global integration name uniqueness while reused "
            "soft-deleted names exist."
        )

    op.drop_index("uq_integrations_active_name", table_name="integrations")
    op.create_unique_constraint("integrations_name_key", "integrations", ["name"])
