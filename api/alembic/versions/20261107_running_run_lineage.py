"""Repair live v1.4.1 executions that never recorded run lineage.

Revision ID: 20261107_running_run_lineage
Revises: 20261106_chat_execute
Create Date: 2026-11-07

``20261023_legacy_run_lineage`` repaired rows still waiting during its
upgrade.  A previous-stable worker can have already claimed a row and leave
it Running or Cancelling across the image update.  Those rows recorded only
``executed_by``.  Recover the same durable semantics as
``shared.run_lineage.recorded_user_lineage``:

* a recorded person is the run user and starter;
* the system account is unattended work, so it uses the existing workflow
  identity or that scope's existing default identity; and
* the row is its own root.

The system account remains the historical recorded actor.  The exact
previous-stable token adapter derives the unattended acting identity from
this durable lineage at authentication time.  Rows with any existing lineage
value, a missing recorded user, or a missing workflow are untouched; the
migration neither invents users nor widens authority.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20261107_running_run_lineage"
down_revision: Union[str, None] = "20261106_chat_execute"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.execute(
        f"""
        WITH live AS (
            SELECT e.id, e.executed_by, e.workflow_id,
                   w.id AS found_workflow, w.run_identity_id,
                   CASE WHEN e.workflow_id IS NULL THEN e.organization_id
                        ELSE w.organization_id END AS scope_org
            FROM executions e
            LEFT JOIN workflows w ON w.id = e.workflow_id
            WHERE e.status IN ('Running', 'Cancelling')
              AND e.run_user_id IS NULL
              AND e.started_by_user_id IS NULL
              AND e.root_execution_id IS NULL
              AND e.executed_by IS NOT NULL
        ),
        recovered AS (
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
            FROM live
        )
        UPDATE executions e
        SET run_user_id = r.run_user_id,
            started_by_user_id = r.run_user_id,
            root_execution_id = e.id
        FROM recovered r
        JOIN users u ON u.id = r.run_user_id
        WHERE e.id = r.id
        """
    )


def downgrade() -> None:
    # Recovered values are indistinguishable from lineage/actors durably
    # recorded by the current runtime; an older release ignores them.
    pass
