"""Link evaluation cases to reviewed findings.

Revision ID: 20260919_case_finding_link
Revises: 20260919_agent_findings
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260919_case_finding_link"
down_revision: str | None = "20260919_agent_findings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_evaluation_cases",
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_eval_cases_finding_id",
        "agent_evaluation_cases",
        "agent_findings",
        ["finding_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_eval_cases_finding_id", "agent_evaluation_cases", ["finding_id"]
    )
    op.drop_constraint(
        "ck_eval_cases_provenance", "agent_evaluation_cases", type_="check"
    )
    op.create_check_constraint(
        "ck_eval_cases_provenance",
        "agent_evaluation_cases",
        "provenance IN ('manual', 'generated', 'historical_inspiration', 'finding')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_eval_cases_provenance", "agent_evaluation_cases", type_="check"
    )
    op.create_check_constraint(
        "ck_eval_cases_provenance",
        "agent_evaluation_cases",
        "provenance IN ('manual', 'generated', 'historical_inspiration')",
    )
    op.drop_index("ix_eval_cases_finding_id", table_name="agent_evaluation_cases")
    op.drop_constraint(
        "fk_eval_cases_finding_id", "agent_evaluation_cases", type_="foreignkey"
    )
    op.drop_column("agent_evaluation_cases", "finding_id")
