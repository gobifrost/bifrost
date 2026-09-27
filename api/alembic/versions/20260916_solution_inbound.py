"""Solution allow_inbound_access (SPIKE).

Inbound counterpart to global_repo_access (outbound). True = this install may
be targeted by per-call ``solution=`` from outside itself (same-scope or
bypass callers, per the scope resolver). False = deterministically isolated:
only own-install calls (caller == target) resolve. Defaults true to preserve
today's open-inbound behavior; sealed is opt-in.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_solution_inbound"
down_revision: str | None = "20260914_anthropic_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "solutions",
        sa.Column(
            "allow_inbound_access",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("solutions", "allow_inbound_access")
