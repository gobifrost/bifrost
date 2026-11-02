"""restore ad-hoc table-name uniqueness after orphan-column removal.

The partial indexes introduced by 20260606_table_name_sol_scope were changed
in 20260609_orphan_tbl_ns to reference ``orphaned_at``. PostgreSQL removed
those indexes when 20260624_sol_status dropped that column. The surviving
solution-install index still owns solution-managed names; this migration
restores only the _repo org/global indexes.

Revision ID: 20261101_table_name_unique
Revises: 20261101_capacity_cleanup
Create Date: 2026-11-01
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import RowMapping


revision: str = "20261101_table_name_unique"
down_revision: Union[str, None] = "20261101_capacity_cleanup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _duplicate_ad_hoc_tables() -> list[RowMapping]:
    return list(
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT
                    organization_id::text AS organization_id,
                    name,
                    array_agg(id::text ORDER BY created_at, id) AS table_ids
                FROM tables
                WHERE solution_id IS NULL
                GROUP BY organization_id, name
                HAVING count(*) > 1
                ORDER BY organization_id NULLS FIRST, name
                """
            )
        )
        .mappings()
        .all()
    )


def _duplicate_error(duplicates: list[RowMapping]) -> RuntimeError:
    conflicts: list[str] = []
    for row in duplicates:
        scope = (
            "global"
            if row["organization_id"] is None
            else f"organization {row['organization_id']}"
        )
        conflicts.append(f"{scope} / {row['name']!r}: {', '.join(row['table_ids'])}")
    return RuntimeError(
        "Cannot restore ad-hoc table name uniqueness because duplicate _repo tables exist. "
        "No rows were changed. Resolve each listed duplicate deliberately, then rerun the "
        f"migration. Conflicts: {'; '.join(conflicts)}"
    )


def upgrade() -> None:
    # Hold the same write-blocking lock as ordinary CREATE UNIQUE INDEX before
    # inspecting the namespace. That makes the preflight disposition cover all
    # rows the indexes will see, including requests still served by an older
    # API image during a rolling rollout.
    op.execute(sa.text("LOCK TABLE tables IN SHARE MODE"))
    duplicates = _duplicate_ad_hoc_tables()
    if duplicates:
        raise _duplicate_error(duplicates)

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
