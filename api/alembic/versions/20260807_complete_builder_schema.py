"""complete Builder ownership and Solution-scoped config schema forward-only

Revision ID: 20260807_complete_builder
Revises: 20260807_platform_job_external

The withdrawn historical revisions remain tombstones. This migration supplies
the two Builder schema groups omitted by the initial forward reinstatement and
is idempotent for databases that briefly observed the original migrations.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_complete_builder"
down_revision: str = "20260807_platform_job_external"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(inspector: sa.Inspector, table: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table)}


def _has_fk(inspector: sa.Inspector, table: str, column: str) -> bool:
    return any(
        fk["constrained_columns"] == [column]
        for fk in inspector.get_foreign_keys(table)
    )


def _add_solution_ownership() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = _columns(inspector, "solutions")
    if "owner_user_id" not in columns:
        op.add_column(
            "solutions",
            sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        )
    if "visibility" not in columns:
        op.add_column(
            "solutions",
            sa.Column(
                "visibility",
                sa.String(length=16),
                nullable=False,
                server_default="shared",
            ),
        )

    inspector = sa.inspect(op.get_bind())
    if not _has_fk(inspector, "solutions", "owner_user_id"):
        op.create_foreign_key(
            "fk_solutions_owner_user_id",
            "solutions",
            "users",
            ["owner_user_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # These two indexes predate visibility. Recreate them with the final
    # predicates so shared and private namespaces cannot collide.
    op.execute(sa.text("DROP INDEX IF EXISTS ix_solutions_slug_org_unique"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_solutions_slug_global_unique"))
    op.create_index(
        "ix_solutions_slug_org_unique",
        "solutions",
        ["slug", "organization_id"],
        unique=True,
        postgresql_where=sa.text(
            "organization_id IS NOT NULL AND visibility = 'shared'"
        ),
    )
    op.create_index(
        "ix_solutions_slug_global_unique",
        "solutions",
        ["slug"],
        unique=True,
        postgresql_where=sa.text(
            "organization_id IS NULL AND visibility = 'shared'"
        ),
    )
    inspector = sa.inspect(op.get_bind())
    indexes = _indexes(inspector, "solutions")
    if "ix_solutions_owner_slug_private_unique" not in indexes:
        op.create_index(
            "ix_solutions_owner_slug_private_unique",
            "solutions",
            ["owner_user_id", "slug"],
            unique=True,
            postgresql_where=sa.text("visibility = 'private'"),
        )
    if "ix_solutions_owner_user_id" not in indexes:
        op.create_index(
            "ix_solutions_owner_user_id",
            "solutions",
            ["owner_user_id"],
        )


def _add_solution_owned_configs() -> None:
    inspector = sa.inspect(op.get_bind())
    if "solution_id" not in _columns(inspector, "configs"):
        op.add_column(
            "configs",
            sa.Column("solution_id", sa.Uuid(), nullable=True),
        )
    inspector = sa.inspect(op.get_bind())
    if not _has_fk(inspector, "configs", "solution_id"):
        op.create_foreign_key(
            "fk_configs_solution_id",
            "configs",
            "solutions",
            ["solution_id"],
            ["id"],
            ondelete="CASCADE",
        )
    if "ix_configs_solution_key_unique" not in _indexes(inspector, "configs"):
        op.create_index(
            "ix_configs_solution_key_unique",
            "configs",
            ["solution_id", "key"],
            unique=True,
            postgresql_where=sa.text("solution_id IS NOT NULL"),
        )


def upgrade() -> None:
    _add_solution_ownership()
    _add_solution_owned_configs()


def downgrade() -> None:
    # Builder schema is intentionally forward-only after the withdrawal.
    pass
