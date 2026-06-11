"""Add 'everyone' to the form/agent access-level enums.

The access tiers are: ``authenticated`` ("Everyone except external users" in
the UI), ``everyone`` (additionally grants to external/portal users),
``role_based``, and ``private`` (agents). Applications and workflows store
access_level as plain VARCHAR, so only the two PG-native enum types need the
new value.

ALTER TYPE ... ADD VALUE cannot run inside a transaction block on older PG,
but Alembic runs each migration in autocommit-compatible mode via
``IF NOT EXISTS`` (the same pattern 20260208 used to add 'private').
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260611_everyone_access"
down_revision = "20260610_user_is_external"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("COMMIT"))
    conn.execute(
        sa.text("ALTER TYPE form_access_level ADD VALUE IF NOT EXISTS 'everyone'")
    )
    conn.execute(
        sa.text("ALTER TYPE agent_access_level ADD VALUE IF NOT EXISTS 'everyone'")
    )


def downgrade() -> None:
    # PG enums can't drop values; converting rows back would require a type
    # rebuild. Rows using 'everyone' must be updated manually before any
    # attempt to remove the value.
    pass
