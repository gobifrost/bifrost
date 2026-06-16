"""merge application-name-branding + pending-captures heads

Merging main into the Solutions branch on 2026-06-16 brought in
``20260615_brand_appname`` (Add Application Name branding setting, #379), whose
chain forks from ``20260604_brand_terms``. The Solutions branch already had
``20260615_pending_captures`` as its head. Both are valid heads that never
rejoin, so ``alembic upgrade head`` fails with "Multiple head revisions are
present". This is a no-op merge revision that unifies them.

Revision ID: 20260616_merge_appname_captures
Revises: 20260615_pending_captures, 20260615_brand_appname
Create Date: 2026-06-16 00:00:00.000000
"""
from alembic import op  # noqa: F401

revision = "20260616_merge_appname_captures"
down_revision = ("20260615_pending_captures", "20260615_brand_appname")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
