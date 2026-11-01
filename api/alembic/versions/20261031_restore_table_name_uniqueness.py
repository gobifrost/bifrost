"""Restore live _repo/ table-name uniqueness.

``20260609_orphan_tbl_ns`` made the two _repo/ partial unique indexes depend
on ``tables.orphaned_at``. PostgreSQL dropped those indexes when
``20260624_sol_status`` later dropped that column, leaving concurrent SDK
auto-create able to insert duplicate _repo/ tables. Solution-owned table names
remain isolated by ``ix_tables_solution_name_unique``.

The preflight deliberately preserves every existing row. Operators must
resolve any duplicate live _repo/ names before the indexes can be restored.

Revision ID: 20261031_table_name_unique
Revises: 20261031_app_sdk_binding
Create Date: 2026-10-31
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261031_table_name_unique"
down_revision: Union[str, None] = "20261031_app_sdk_binding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DUPLICATE_GLOBAL_NAMES = sa.text(
    """
    SELECT name
    FROM tables
    WHERE organization_id IS NULL AND solution_id IS NULL
    GROUP BY name
    HAVING count(*) > 1
    ORDER BY name
    LIMIT 10
    """
)
_DUPLICATE_ORG_NAMES = sa.text(
    """
    SELECT organization_id::text || ':' || name
    FROM tables
    WHERE organization_id IS NOT NULL AND solution_id IS NULL
    GROUP BY organization_id, name
    HAVING count(*) > 1
    ORDER BY organization_id, name
    LIMIT 10
    """
)


def _duplicate_names(statement: sa.TextClause) -> list[str]:
    return list(op.get_bind().execute(statement).scalars())


def upgrade() -> None:
    global_duplicates = _duplicate_names(_DUPLICATE_GLOBAL_NAMES)
    org_duplicates = _duplicate_names(_DUPLICATE_ORG_NAMES)
    if global_duplicates or org_duplicates:
        details: list[str] = []
        if global_duplicates:
            details.append(f"global={global_duplicates}")
        if org_duplicates:
            details.append(f"organization={org_duplicates}")
        raise RuntimeError(
            "Cannot restore live _repo/ table-name uniqueness because duplicate "
            "rows already exist (up to 10 identifiers per scope): "
            + "; ".join(details)
            + ". Resolve or rename the duplicates, then rerun this migration; "
            "no table rows were changed."
        )

    op.create_index(
        "ix_tables_org_name_unique",
        "tables",
        ["organization_id", "name"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL AND solution_id IS NULL"),
    )
    op.create_index(
        "ix_tables_global_name_unique",
        "tables",
        ["name"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL AND solution_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_tables_global_name_unique", table_name="tables")
    op.drop_index("ix_tables_org_name_unique", table_name="tables")
