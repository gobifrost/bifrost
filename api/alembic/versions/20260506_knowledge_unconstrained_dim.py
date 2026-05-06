"""knowledge_store: drop fixed 1536-dim Vector constraint, allow any dim

Revision ID: 20260506_knowledge_dim
Revises: 20260503_agent_mcp_grants
Create Date: 2026-05-06

Background: knowledge_store.embedding was declared `vector(1536)` because the
embedding card hardcoded `text-embedding-3-small`. With model selection now
flexible, picking a model with a different output dim (e.g. Gemini's 3072 or
Cohere's 1024) failed at INSERT time.

Switch the column to unconstrained `vector` so any dim fits. Drop the
ivfflat index — it's tied to a specific dim and can't span multiple sizes.
At our scale sequential scan is fine; if/when index rebuild is needed, do it
per-dim or behind a migration that knows the configured embedding dim.

Existing rows are unaffected: pgvector tolerates the column type relaxation.
"""

from alembic import op


revision = "20260506_knowledge_dim"
down_revision = "20260503_agent_mcp_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the dim-specific ANN index — it can't survive a column-type change
    # and it's incompatible with mixed-dim rows anyway.
    op.execute("DROP INDEX IF EXISTS ix_knowledge_embedding")

    # Relax the column from vector(1536) to plain vector. Existing rows keep
    # their length (still 1536) but the column will now accept any dimension.
    op.execute(
        "ALTER TABLE knowledge_store "
        "ALTER COLUMN embedding TYPE vector USING embedding::vector"
    )


def downgrade() -> None:
    # Refuse-and-fail downgrade if any row has a non-1536-dim vector — the
    # alternative is silently truncating real data.
    op.execute(
        """
        DO $$
        DECLARE
            bad_count INT;
        BEGIN
            SELECT count(*) INTO bad_count
            FROM knowledge_store
            WHERE vector_dims(embedding) <> 1536;
            IF bad_count > 0 THEN
                RAISE EXCEPTION
                    'Cannot downgrade: % rows have non-1536-dim embeddings. '
                    'Re-embed or delete them first.', bad_count;
            END IF;
        END $$;
        """
    )
    op.execute(
        "ALTER TABLE knowledge_store "
        "ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector(1536)"
    )
    op.execute(
        """
        CREATE INDEX ix_knowledge_embedding ON knowledge_store
        USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
        """
    )
