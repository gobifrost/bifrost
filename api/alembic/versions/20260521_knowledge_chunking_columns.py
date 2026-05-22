"""knowledge_chunking_columns

Adds chunk_index and chunk_count to knowledge_store, and widens the
uniqueness constraint to include chunk_index so multiple chunks of the
same key can coexist.

Existing rows get chunk_index=0, chunk_count=1, which is byte-identical
to today's "one row per doc" behavior. No data migration needed —
re-chunking happens lazily on the next store() or reindex.

Revision ID: 20260521_knowledge_chunking
Revises: 20260521_merge_logos_topic
Create Date: 2026-05-21
"""
from alembic import op
import sqlalchemy as sa

revision = "20260521_knowledge_chunking"
down_revision = "20260521_merge_logos_topic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_store",
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "knowledge_store",
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="1"),
    )

    # Drop the old (ns, org, key) unique constraint and replace with
    # (ns, org, key, chunk_index). postgresql_nulls_not_distinct preserves
    # the existing "treat NULL org_id as equal" semantic.
    op.drop_constraint("uq_knowledge_ns_org_key", "knowledge_store", type_="unique")
    op.create_unique_constraint(
        "uq_knowledge_ns_org_key_chunk",
        "knowledge_store",
        ["namespace", "organization_id", "key", "chunk_index"],
        postgresql_nulls_not_distinct=True,
    )

    # Non-unique lookup index for "find all chunks of this doc" — used by
    # reindex grouping and by search dedup. Restricted to non-null keys
    # because key-less docs can't be grouped.
    op.create_index(
        "ix_knowledge_ns_org_key",
        "knowledge_store",
        ["namespace", "organization_id", "key"],
        postgresql_where=sa.text("key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_ns_org_key", table_name="knowledge_store")
    op.drop_constraint("uq_knowledge_ns_org_key_chunk", "knowledge_store", type_="unique")
    op.create_unique_constraint(
        "uq_knowledge_ns_org_key",
        "knowledge_store",
        ["namespace", "organization_id", "key"],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_column("knowledge_store", "chunk_count")
    op.drop_column("knowledge_store", "chunk_index")
