"""Require the authority-version release to have rolled out before claims.

Revision ID: 20261031_authz_claims_precondition
Revises: 20261030_authz_versions
Create Date: 2026-10-31

Token PR1b (docs/plans/2026-10-30-token-pr1-implementation-plan.md §8.2):
from this release, runtime access tokens carry their authorization facts and
are validated against the authority epoch, and runs refresh through
``/auth/refresh``. An API from before the previous token release can neither
redeem a run refresh token nor keep the epoch honest, so this release may
only be installed once that release (protocol 1) has started.

The upgrade refuses unless this run began on an empty schema (a fresh
install, where no older pod, token or run can exist) or an API of protocol 1
has recorded its start in ``authz_protocol_state``. That blocks
skip-upgrades and restores of backups taken before the previous release. A
fresh install records protocol 2 itself; upgraded installs record it when
their first new API starts.

No locks beyond a read of ``authz_protocol_state`` (and, on a fresh install,
one insert into it).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op


revision: str = "20261031_authz_claims_precondition"
down_revision: Union[str, None] = "20261030_authz_versions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Set by alembic/env.py before any migration of this run executes.
_STARTING_REVISION = "bifrost_starting_revision"

PRECONDITION_MESSAGE = (
    "Upgrade to the release that added the authority version (migration "
    "20261030_authz_versions, token release PR1a) and let it finish rolling "
    "out first: no API of that release has recorded its start in "
    "authz_protocol_state. See docs/runbooks/authz-epoch.md."
)


def _started_on_empty_schema() -> bool:
    """Whether this upgrade run began on an empty schema (a fresh install)."""
    attributes = context.config.attributes
    return _STARTING_REVISION in attributes and attributes[_STARTING_REVISION] is None


def upgrade() -> None:
    if _started_on_empty_schema():
        op.execute(
            "INSERT INTO authz_protocol_state (protocol, first_started_at) VALUES (2, now())"
        )
        return
    started = op.get_bind().execute(
        sa.text("SELECT 1 FROM authz_protocol_state WHERE protocol = 1")
    ).first()
    if started is None:
        raise RuntimeError(PRECONDITION_MESSAGE)


def downgrade() -> None:
    # The protocol records are durable rollout evidence; nothing to undo.
    pass
