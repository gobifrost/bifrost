"""Bind Solution installs to scoped private GitHub sources."""
from alembic import op
import sqlalchemy as sa

revision = "20261109_solution_git_source"
down_revision = "20261109_merge_calibration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("solutions", sa.Column("git_source_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_solutions_git_source", "solutions", "system_configs", ["git_source_id"], ["id"], ondelete="NO ACTION")


def downgrade() -> None:
    op.drop_constraint("fk_solutions_git_source", "solutions", type_="foreignkey")
    op.drop_column("solutions", "git_source_id")
