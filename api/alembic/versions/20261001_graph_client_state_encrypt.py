"""Encrypt Microsoft Graph subscription clientState at rest

Revision ID: 20261001_graph_client_state_enc
Revises: 20261001_webhook_secret_enc
Create Date: 2026-10-01

A Microsoft Graph webhook source registers a server-generated ``clientState``
with Graph and checks it on every notification. It was stored in plaintext in
``webhook_sources.state["client_state"]``. It is now stored only as ciphertext
in ``state["client_state_encrypted"]``, written with ``encrypt_secret``, and
the adapter decrypts it only to validate a notification.

For each row holding a plaintext ``client_state``, this migration encrypts it
into ``client_state_encrypted`` and removes the plaintext key, so a live
subscription keeps validating against exactly the value Graph already holds.

Rows without a plaintext ``client_state`` are left alone, so running the data
step again changes nothing. Downgrade is a no-op: restoring plaintext would
undo the point of the change. Code from before this change reads only the
plaintext key, so after a revert a migrated subscription has nothing to check
and stops validating ``clientState`` until it is resubscribed.
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "20261001_graph_client_state_enc"
down_revision: Union[str, None] = "20261001_webhook_secret_enc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PLAINTEXT_KEY = "client_state"
ENCRYPTED_KEY = "client_state_encrypted"


def encrypt_plaintext_graph_client_state(connection: Connection) -> None:
    # The live cipher, so the adapter can decrypt what this writes.
    from src.core.security import encrypt_secret

    rows = connection.execute(
        sa.text(
            "SELECT id, state FROM webhook_sources WHERE (state -> :key) IS NOT NULL"
        ),
        {"key": PLAINTEXT_KEY},
    ).fetchall()
    for row in rows:
        state = dict(row.state or {})
        plaintext = state.pop(PLAINTEXT_KEY, None)
        if isinstance(plaintext, str) and plaintext and ENCRYPTED_KEY not in state:
            state[ENCRYPTED_KEY] = encrypt_secret(plaintext)
        connection.execute(
            sa.text(
                "UPDATE webhook_sources SET state = CAST(:state AS jsonb) WHERE id = :id"
            ),
            {"id": row.id, "state": json.dumps(state)},
        )


def upgrade() -> None:
    encrypt_plaintext_graph_client_state(op.get_bind())


def downgrade() -> None:
    pass
