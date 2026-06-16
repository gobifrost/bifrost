"""add branding application_name

Revision ID: 20260615_brand_appname
Revises: 20260604_brand_terms
Create Date: 2026-06-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20260615_brand_appname"
down_revision: Union[str, Sequence[str]] = "20260604_brand_terms"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "branding",
        sa.Column("application_name", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("branding", "application_name")
