"""Add the testing model assignment for the Evaluation Test Designer.

Revision ID: 20260919_testing_assignment
Revises: 20260919_runtime_caller_auth
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260919_testing_assignment"
down_revision: str | None = "20260919_runtime_caller_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ASSIGNMENT_VALUES = (
    "'primary', 'summarization', 'tuning', "
    "'image_generation', 'video_generation', 'chat_default'"
)
NEW_ASSIGNMENT_VALUES = (
    "'primary', 'summarization', 'testing', 'tuning', "
    "'image_generation', 'video_generation', 'chat_default'"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_ai_model_assignments_key", "ai_model_assignments", type_="check"
    )
    op.create_check_constraint(
        "ck_ai_model_assignments_key",
        "ai_model_assignments",
        sa.text(f"assignment_key IN ({NEW_ASSIGNMENT_VALUES})"),
    )
    # Explicit one-time initialization: the testing designer inherits the
    # existing primary profile. There is no runtime fallback — after this
    # migration a missing 'testing' row is a configuration error (HTTP 422),
    # never a silent substitution.
    op.execute(
        sa.text(
            "INSERT INTO ai_model_assignments (assignment_key, profile_id) "
            "SELECT 'testing', profile_id FROM ai_model_assignments "
            "WHERE assignment_key = 'primary' "
            "ON CONFLICT (assignment_key) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM ai_model_assignments WHERE assignment_key = 'testing'"
        )
    )
    op.drop_constraint(
        "ck_ai_model_assignments_key", "ai_model_assignments", type_="check"
    )
    op.create_check_constraint(
        "ck_ai_model_assignments_key",
        "ai_model_assignments",
        sa.text(f"assignment_key IN ({OLD_ASSIGNMENT_VALUES})"),
    )
