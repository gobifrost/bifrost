"""solution_deploy_jobs orchestration table

Tracks an async, observable solution deploy: the deploy endpoint enqueues a
background task and returns the job id immediately; the task flips
queued -> running -> succeeded/failed (capturing the error). The CLI polls the
status endpoint until a terminal status, so it no longer times out client-side
while the server completes the (sometimes >100s) deploy.

Revision ID: 20260617_solution_deploy_jobs
Revises: 20260616_merge_appname_captures
"""

import sqlalchemy as sa
from alembic import op

revision = "20260617_solution_deploy_jobs"
down_revision = "20260616_merge_appname_captures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "solution_deploy_jobs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("install_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["install_id"], ["solutions.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_solution_deploy_jobs_install_id",
        "solution_deploy_jobs",
        ["install_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_solution_deploy_jobs_install_id", table_name="solution_deploy_jobs"
    )
    op.drop_table("solution_deploy_jobs")
