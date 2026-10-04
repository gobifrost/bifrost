"""Audit retention: archive segment catalog, existing-install policy, paging index

Revision ID: 20261005_audit_retention
Revises: 20261004_r3b_lineage
Create Date: 2026-10-05

audit_archive_segments catalogs audit events archived to object storage.
organization_id and platform_job_id carry no foreign keys: segments must
outlive the organization and the job that wrote them.

New installs get the default policy (archive at 90 days, delete archives at
365). An existing install that already holds audit events gets an explicit
policy that archives at 90 days and keeps archives forever, so upgrading
never deletes history. Installs with no audit events take the default.

ix_audit_logs_created_id pages the archiver through events in
(created_at, id) order; it is built CONCURRENTLY so the large audit_logs
table stays writable.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261005_audit_retention"
down_revision: Union[str, None] = "20261004_r3b_lineage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EXISTING_INSTALL_POLICY_SQL = """
INSERT INTO system_configs
       (id, category, key, value_json, organization_id, created_at, updated_at, created_by, updated_by)
SELECT gen_random_uuid(), 'audit_retention', 'policy',
       '{"hot_days": 90, "archive_days": null}'::jsonb, NULL, NOW(), NOW(), 'migration', 'migration'
 WHERE EXISTS (SELECT 1 FROM audit_logs)
   AND NOT EXISTS (SELECT 1 FROM system_configs
                    WHERE category = 'audit_retention' AND key = 'policy' AND organization_id IS NULL)
"""


def upgrade() -> None:
    op.create_table(
        "audit_archive_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("first_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("platform_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index(
        "ix_audit_archive_segments_org_day",
        "audit_archive_segments",
        ["organization_id", "day"],
    )
    op.create_index(
        "ix_audit_archive_segments_last_created",
        "audit_archive_segments",
        ["last_created_at"],
    )

    op.execute(EXISTING_INSTALL_POLICY_SQL)

    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_audit_logs_created_id "
            "ON audit_logs (created_at, id)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_audit_logs_created_id")
    op.execute(
        "DELETE FROM system_configs "
        "WHERE category = 'audit_retention' AND key = 'policy' AND organization_id IS NULL"
    )
    op.drop_table("audit_archive_segments")
