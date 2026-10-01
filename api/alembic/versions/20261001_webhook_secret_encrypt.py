"""Encrypt webhook signing secrets and remove them from adapter config

Revision ID: 20261001_webhook_secret_enc
Revises: 20261001_r3a_operator_perms
Create Date: 2026-10-01

A generic webhook source's HMAC signing secret was stored in plaintext twice:
in ``webhook_sources.config["secret"]``, which the events API returned and
manifests and Solution bundles carried, and in ``webhook_sources.state["secret"]``,
which the adapter read to verify requests. The secret is now write-only and
lives only in ``state["secret_encrypted"]``, encrypted with ``encrypt_secret``.

For each row holding a plaintext secret, this migration encrypts
``state["secret"]`` into ``state["secret_encrypted"]`` and removes ``"secret"``
from both config and state. A source therefore keeps verifying with exactly the
secret it verifies with today.

A secret found only in config was never enforced: verification has always read
state, and Solution deploy and git-sync import wrote config without state.
Such a secret is dropped rather than switched on, so no sender starts failing
signature checks. An operator can set it with the rotate-secret endpoint.

Rows without a plaintext secret are left alone, so running the data step again
changes nothing. Downgrade is a no-op: restoring plaintext secrets would undo
the point of the change.
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "20261001_webhook_secret_enc"
down_revision: Union[str, None] = "20261001_r3a_operator_perms"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PLAINTEXT_KEY = "secret"
ENCRYPTED_KEY = "secret_encrypted"


def encrypt_plaintext_webhook_secrets(connection: Connection) -> None:
    # The live cipher, so the adapter can decrypt what this writes.
    from src.core.security import encrypt_secret

    rows = connection.execute(
        sa.text(
            "SELECT id, config, state FROM webhook_sources "
            "WHERE (config -> :key) IS NOT NULL OR (state -> :key) IS NOT NULL"
        ),
        {"key": PLAINTEXT_KEY},
    ).fetchall()
    for row in rows:
        config = dict(row.config or {})
        state = dict(row.state or {})
        config.pop(PLAINTEXT_KEY, None)
        enforced = state.pop(PLAINTEXT_KEY, None)
        if isinstance(enforced, str) and enforced and ENCRYPTED_KEY not in state:
            state[ENCRYPTED_KEY] = encrypt_secret(enforced)
        connection.execute(
            sa.text(
                "UPDATE webhook_sources SET config = CAST(:config AS jsonb), "
                "state = CAST(:state AS jsonb) WHERE id = :id"
            ),
            {"id": row.id, "config": json.dumps(config), "state": json.dumps(state)},
        )


def upgrade() -> None:
    encrypt_plaintext_webhook_secrets(op.get_bind())


def downgrade() -> None:
    pass
