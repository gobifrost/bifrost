"""add Builder model profile assignment

Revision ID: 20260827_builder_model_profile
Revises: 20260826_usage_limits, 20260823_job_memory_profiles
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260827_builder_model_profile"
down_revision: tuple[str, str] = (
    "20260826_usage_limits",
    "20260823_job_memory_profiles",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_ai_model_assignments_key",
        "ai_model_assignments",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ai_model_assignments_key",
        "ai_model_assignments",
        "assignment_key IN ('primary', 'summarization', 'tuning', "
        "'image_generation', 'video_generation', 'chat_default', 'builder')",
    )
    op.execute(
        """
        INSERT INTO ai_model_assignments (
            assignment_key,
            profile_id,
            created_at,
            updated_at
        )
        SELECT 'builder', profile_id, NOW(), NOW()
        FROM ai_model_assignments
        WHERE assignment_key = 'primary'
        ON CONFLICT (assignment_key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM ai_model_assignments WHERE assignment_key = 'builder'")
    op.drop_constraint(
        "ck_ai_model_assignments_key",
        "ai_model_assignments",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ai_model_assignments_key",
        "ai_model_assignments",
        "assignment_key IN ('primary', 'summarization', 'tuning', "
        "'image_generation', 'video_generation', 'chat_default')",
    )
