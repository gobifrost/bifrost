"""add per-job memory requirements and learned platform-job memory profiles

Revision ID: 20260823_job_memory_profiles
Revises: 20260822_ai_model_profiles
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_job_memory_profiles"
down_revision: str | Sequence[str] = "20260822_ai_model_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "platform_jobs",
        sa.Column("memory_profile_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "platform_jobs",
        sa.Column(
            "memory_required_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("268435456"),
        ),
    )
    op.create_table(
        "platform_job_memory_profiles",
        sa.Column("profile_key", sa.String(length=255), primary_key=True),
        sa.Column("memory_required_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "observed_high_water_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "sample_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("platform_job_memory_profiles")
    op.drop_column("platform_jobs", "memory_required_bytes")
    op.drop_column("platform_jobs", "memory_profile_key")
