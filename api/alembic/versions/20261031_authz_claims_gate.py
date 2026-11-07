"""Record the claims protocol on fresh installs.

Revision ID: 20261031_authz_claims_gate
Revises: 20261030_authz_versions
Create Date: 2026-10-31

Stable installations can upgrade directly: legacy runtime credentials resolve
their authority from durable run records, and human clients sign in again.
Requiring an intermediate development API to have started did not establish
that all older APIs had stopped, and prevented supported stable upgrades.
Keep this revision and the fresh-install protocol record. Existing databases
record protocol 2 only when the new API actually starts; migration does not
invent evidence of a protocol-1 deployment.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op


revision: str = "20261031_authz_claims_gate"
down_revision: Union[str, None] = "20261030_authz_versions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Set by alembic/env.py before any migration of this run executes.
_STARTING_REVISION = "bifrost_starting_revision"

def _started_on_empty_schema() -> bool:
    """Whether this upgrade run began on an empty schema (a fresh install)."""
    attributes = context.config.attributes
    return _STARTING_REVISION in attributes and attributes[_STARTING_REVISION] is None


def upgrade() -> None:
    if _started_on_empty_schema():
        op.execute(
            "INSERT INTO authz_protocol_state (protocol, first_started_at) VALUES (2, now())"
        )


def downgrade() -> None:
    # The protocol records are durable rollout evidence; nothing to undo.
    pass
