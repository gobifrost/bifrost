"""Merge compute isolation and identity access migration histories."""

revision = "20261120_compute_identity_merge"
down_revision = ("20261115_ext_isolated_job", "20261117_idp_access_pending")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
