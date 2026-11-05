"""Runs an earlier release left waiting record who they run for

Revision ID: 20261023_legacy_run_lineage
Revises: 20261023_worker_memory_pressure
Create Date: 2026-10-23

Releases before run lineage (v1.4.1 and earlier) recorded only the user a run
executes as. A run such a release scheduled or queued, still waiting when the
install upgrades, has no lineage, and the runtime refuses to run it. Each one
now runs for the user it recorded, as that release would have run it (the
rule in shared.run_lineage.recorded_user_lineage):

- a person: that person is the run user and the starter;
- the system account (a schedule, webhook or other unattended start): the
  workflow's identity, else the default identity of the workflow's
  organization (the global identity for a global workflow), as a schedule
  does today. An inline script uses its organization's default identity.

The run is its own root. A run whose recorded user or workflow is gone keeps
no lineage and fails visibly. Rows that already record lineage are untouched.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20261023_legacy_run_lineage"
down_revision: Union[str, None] = "20261023_worker_memory_pressure"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.execute(
        f"""
        WITH waiting AS (
            SELECT e.id, e.executed_by, e.workflow_id,
                   w.id AS found_workflow, w.run_identity_id,
                   CASE WHEN e.workflow_id IS NULL THEN e.organization_id
                        ELSE w.organization_id END AS scope_org
            FROM executions e
            LEFT JOIN workflows w ON w.id = e.workflow_id
            WHERE e.status IN ('Scheduled', 'Pending')
              AND e.run_user_id IS NULL
              AND e.executed_by IS NOT NULL
        ),
        recorded AS (
            SELECT id,
                   CASE
                     WHEN executed_by <> '{SYSTEM_USER_ID}'::uuid THEN executed_by
                     WHEN workflow_id IS NOT NULL AND found_workflow IS NULL THEN NULL
                     WHEN run_identity_id IS NOT NULL THEN run_identity_id
                     WHEN scope_org IS NULL THEN (
                       SELECT u.id FROM users u WHERE u.identity_kind = 'global_default'
                     )
                     ELSE (
                       SELECT u.id FROM users u
                       WHERE u.identity_kind = 'org_default' AND u.organization_id = scope_org
                     )
                   END AS run_user_id
            FROM waiting
        )
        UPDATE executions e
        SET run_user_id = r.run_user_id,
            started_by_user_id = r.run_user_id,
            root_execution_id = e.id
        FROM recorded r
        JOIN users u ON u.id = r.run_user_id
        WHERE e.id = r.id
        """
    )


def downgrade() -> None:
    # The backfilled lineage is indistinguishable from lineage recorded at
    # acceptance; an older release ignores it.
    pass
