"""Drop workflows.code and workflows.code_hash columns.

Code now lives in file_index table and _repo/ S3 prefix.

Revision ID: 20260210_drop_code
Revises: 20260210_file_index
Create Date: 2026-02-10
"""

from alembic import op
import sqlalchemy as sa

revision = "20260210_drop_code"
down_revision = "20260210_file_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve the last database-held source before removing its columns.
    # A newer indexed copy wins; several functions may share one source path.
    op.execute(sa.text("""
        INSERT INTO file_index (path, content, content_hash, updated_at)
        SELECT DISTINCT ON (path) path, code, code_hash, COALESCE(updated_at, NOW())
        FROM workflows
        WHERE path IS NOT NULL AND code IS NOT NULL
        ORDER BY path, updated_at DESC NULLS LAST, id
        ON CONFLICT (path) DO NOTHING
    """))
    op.drop_column("workflows", "code")
    op.drop_column("workflows", "code_hash")


def downgrade() -> None:
    op.add_column("workflows", sa.Column("code_hash", sa.String(64), nullable=True))
    op.add_column("workflows", sa.Column("code", sa.Text(), nullable=True))
