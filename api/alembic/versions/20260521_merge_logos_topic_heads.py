"""merge entity_logos and topic_sources heads

Revision ID: 20260521_merge_logos_topic
Revises: 20260519_add_entity_logos, 20260521_topic_sources
Create Date: 2026-05-21

No-op merge: unifies the two alembic heads that diverged when the
invite-flow branch (topic sources / event subsystem rework) and main
(entity_logos) developed in parallel.
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "20260521_merge_logos_topic"
down_revision: Union[str, Sequence[str]] = (
    "20260519_add_entity_logos",
    "20260521_topic_sources",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
