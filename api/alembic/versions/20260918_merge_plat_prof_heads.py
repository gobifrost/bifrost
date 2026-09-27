"""merge platform_job_k8s_ns and profile_max_tokens heads

Revision ID: 20260918_merge_plat_prof_heads
Revises: 20260915_platform_job_k8s_ns, 20260917_profile_max_tokens
Create Date: 2026-09-18

No-op merge: unifies the two alembic heads that diverged when the
platform k8s-namespace work on main and this branch's profile
default_max_tokens work developed in parallel.
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "20260918_merge_plat_prof_heads"
down_revision: Union[str, Sequence[str]] = (
    "20260915_platform_job_k8s_ns",
    "20260917_profile_max_tokens",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
