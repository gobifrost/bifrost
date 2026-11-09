"""Join worker calibration and the workspace/slot upgrade lineage."""

revision = "20261109_merge_calibration"
down_revision = ("20261108_worker_calibration", "20261109_deprecate_slot_fields")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
