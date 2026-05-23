"""merge custom_claims and knowledge_chunking heads

Revision ID: 20260522_merge_claims_knowledge
Revises: 20260521_add_custom_claims, 20260521_knowledge_chunking
Create Date: 2026-05-22

No-op merge: unifies the two alembic heads that diverged when the
custom_claims branch and the knowledge_chunking landed in parallel.
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "20260522_merge_claims_knowledge"
down_revision: Union[str, Sequence[str]] = (
    "20260521_add_custom_claims",
    "20260521_knowledge_chunking",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
