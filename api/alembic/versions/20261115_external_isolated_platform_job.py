"""Reserve external isolated workers for one queued platform-job attempt."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261115_ext_isolated_job"
down_revision = "20261114_job_executor_attr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "execution_workers",
        sa.Column("isolated_platform_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "execution_workers",
        sa.Column("isolated_platform_job_attempt", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_execution_workers_isolated_platform_job_pair",
        "execution_workers",
        "(isolated_platform_job_id IS NULL) = (isolated_platform_job_attempt IS NULL) "
        "AND (isolated_platform_job_attempt IS NULL OR isolated_platform_job_attempt > 0)",
    )
    op.create_index(
        "uq_execution_workers_external_isolated_platform_job",
        "execution_workers",
        ["isolated_platform_job_id"],
        unique=True,
        postgresql_where=sa.text(
            "form = 'external' AND isolated_platform_job_id IS NOT NULL "
            "AND capacity_resource_deleted_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_execution_workers_external_isolated_platform_job", table_name="execution_workers")
    op.drop_constraint(
        "ck_execution_workers_isolated_platform_job_pair",
        "execution_workers",
        type_="check",
    )
    op.drop_column("execution_workers", "isolated_platform_job_attempt")
    op.drop_column("execution_workers", "isolated_platform_job_id")
