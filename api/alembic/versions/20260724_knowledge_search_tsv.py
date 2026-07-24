"""add full-text index for hybrid knowledge retrieval

Revision ID: 20260724_knowledge_search_tsv
Revises: 20260723_exec_started_idx
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260724_knowledge_search_tsv"
down_revision: str = "20260723_exec_started_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE knowledge_store ADD COLUMN search_tsv tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(key, '')), 'A') ||
            setweight(
                to_tsvector('english', coalesce(metadata ->> 'title', '')),
                'A'
            ) ||
            setweight(
                to_tsvector('english', coalesce(metadata ->> 'parent_slug', '')),
                'B'
            ) ||
            setweight(
                to_tsvector(
                    'english',
                    coalesce(metadata ->> 'faq_breadcrumbs', '')
                ),
                'B'
            ) ||
            setweight(to_tsvector('english', coalesce(content, '')), 'C')
        ) STORED
    """)
    op.create_index(
        "ix_knowledge_search_tsv_gin",
        "knowledge_store",
        ["search_tsv"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_search_tsv_gin",
        table_name="knowledge_store",
    )
    op.execute("ALTER TABLE knowledge_store DROP COLUMN search_tsv")
