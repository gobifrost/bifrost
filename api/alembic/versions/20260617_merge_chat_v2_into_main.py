"""Merge chat_v2 M3 branch into main

After catching `feature/chat-v2` up with main (Solutions + Application Name +
pending-captures), two alembic heads coexist:

- ``20260429_chat_v2_m3`` — chat-v2 M3 message-branching models
- ``20260616_merge_appname_captures`` — main's tip

Both branches stand on their own; this is a no-op merge revision that unifies
them so ``alembic upgrade head`` resolves to a single head.

Revision ID: 20260617_merge_chatv2_main
Revises: 20260429_chat_v2_m3, 20260616_merge_appname_captures
Create Date: 2026-06-17

"""

# revision identifiers, used by Alembic.
revision: str = "20260617_merge_chatv2_main"
down_revision: tuple[str, str] = (
    "20260429_chat_v2_m3",
    "20260616_merge_appname_captures",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
