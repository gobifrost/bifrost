"""Withdrawn private Solution promotion schema revision.

The unfinished Solution Builder briefly shipped on main. Keep its revision ID in
Alembic's graph so databases that observed it remain upgradeable. Fresh
databases intentionally perform no work here; the forward compatibility
revision removes deterministic seed data while leaving existing schema inert.
"""

from collections.abc import Sequence

revision: str = "20260727_promotion_pinning"
down_revision: str | None = "20260727_durable_deploy_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
