"""solution repo_subpath, git_ref, update_available_version

Revision ID: 15f86c4cbc4c
Revises: 20260614_solution_conn_refs
Create Date: 2026-06-14 19:33:48.341055+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '15f86c4cbc4c'
down_revision: Union[str, None] = '20260614_solution_conn_refs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("solutions", sa.Column("repo_subpath", sa.String(1024), nullable=True))
    op.add_column("solutions", sa.Column("git_ref", sa.String(255), nullable=True))
    op.add_column("solutions", sa.Column("update_available_version", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("solutions", "update_available_version")
    op.drop_column("solutions", "git_ref")
    op.drop_column("solutions", "repo_subpath")
