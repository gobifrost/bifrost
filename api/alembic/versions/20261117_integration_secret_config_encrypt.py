"""Encrypt integration config values whose schema field is secret

Revision ID: 20261117_integ_secret_encrypt
Revises: 20261116_organization_tags
Create Date: 2026-11-17

An integration config value is a secret when its config-schema field has
type ``secret``. Two gaps let such values be stored without encryption: one
mapping write path saved values verbatim, and changing a field's type to
``secret`` left already-saved values untouched. This migration brings every
such value to the shape the platform now maintains: ``config_type = 'secret'``
with the value encrypted by ``encrypt_secret``.

For each config row joined to a ``secret`` schema field:

- an empty value is relabelled ``secret``;
- a value that decrypts with the live key is relabelled ``secret`` unchanged;
- a plaintext text value is encrypted and relabelled ``secret``;
- a ``secret`` row already holding ciphertext is left alone.

Anything else stops the migration with the row id and key (never the value):
a non-text value, or a non-``secret`` row holding ciphertext the live key
cannot decrypt. Nothing is rewritten silently. Running the data step again
changes nothing. Downgrade is a no-op: restoring plaintext would undo the
point of the change.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "20261117_integ_secret_encrypt"
down_revision: Union[str, None] = "20261116_organization_tags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Version byte + timestamp + IV + HMAC + one AES block.
_MIN_FERNET_TOKEN_BYTES = 1 + 8 + 16 + 32 + 16


def _looks_encrypted(value: str) -> bool:
    """Whether ``value`` has the shape of ``encrypt_secret`` output."""
    try:
        token = base64.urlsafe_b64decode(value.encode())
        raw = base64.urlsafe_b64decode(token)
    except (binascii.Error, ValueError):
        return False
    return len(raw) >= _MIN_FERNET_TOKEN_BYTES and raw[0] == 0x80


def encrypt_schema_secret_config_values(connection: Connection) -> None:
    # The live cipher, so the API can decrypt what this writes.
    from src.core.security import decrypt_secret, encrypt_secret

    rows = connection.execute(
        sa.text(
            "SELECT c.id, c.key, c.value, CAST(c.config_type AS text) AS config_type "
            "FROM configs c "
            "JOIN integration_config_schema ics "
            "  ON ics.integration_id = c.integration_id AND ics.key = c.key "
            "WHERE ics.type = 'secret'"
        )
    ).fetchall()

    for row in rows:
        stored: Any = row.value
        raw = stored.get("value") if isinstance(stored, dict) else stored
        is_secret_row = row.config_type == "secret"

        if raw is None or raw == "":
            if not is_secret_row:
                _relabel(connection, row.id)
            continue
        if not isinstance(raw, str):
            raise RuntimeError(
                f"Integration config {row.id} (key {row.key!r}) belongs to a secret "
                f"field but stores a {type(raw).__name__} value; clear or re-enter it "
                "and run the migration again"
            )

        try:
            decrypt_secret(raw)
            decrypts = True
        except Exception:
            decrypts = False

        if decrypts:
            if not is_secret_row:
                _relabel(connection, row.id)
        elif _looks_encrypted(raw):
            if not is_secret_row:
                raise RuntimeError(
                    f"Integration config {row.id} (key {row.key!r}) belongs to a "
                    "secret field and holds ciphertext the current key cannot "
                    "decrypt; re-enter it and run the migration again"
                )
        else:
            connection.execute(
                sa.text(
                    "UPDATE configs SET config_type = CAST('secret' AS config_type), "
                    "value = jsonb_build_object('value', CAST(:value AS text)) "
                    "WHERE id = :id"
                ),
                {"id": row.id, "value": encrypt_secret(raw)},
            )


def _relabel(connection: Connection, config_id: Any) -> None:
    connection.execute(
        sa.text(
            "UPDATE configs SET config_type = CAST('secret' AS config_type) WHERE id = :id"
        ),
        {"id": config_id},
    )


def upgrade() -> None:
    encrypt_schema_secret_config_values(op.get_bind())


def downgrade() -> None:
    pass
