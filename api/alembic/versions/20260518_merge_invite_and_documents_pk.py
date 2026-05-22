"""merge invite and documents pk heads

Revision ID: 20260518_merge_invite_and_documents_pk
Revises: 20260508_add_user_invites, 20260518_documents_composite_pk
Create Date: 2026-05-18

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "20260518_merge_heads"
down_revision: Union[str, Sequence[str]] = (
    "20260508_user_invites",
    "20260518_documents_composite_pk",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
