"""Merge runtime attribution and identity access migration histories."""

revision = "20261120_runtime_identity_merge"
down_revision = ("20261114_job_executor_attr", "20261117_idp_access_pending")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
