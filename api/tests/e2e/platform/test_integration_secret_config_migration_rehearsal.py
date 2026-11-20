"""True pre-head rehearsal for
``alembic/versions/20261117_integration_secret_config_encrypt.py``.

Builds a disposable database at the migration's ``down_revision``, seeds
integration config rows in the shapes the old write paths left behind, upgrades
to head, and checks that:

- a plaintext value of a secret field (stored STRING, or SECRET overwritten
  with plaintext) is encrypted and labelled ``secret``;
- ciphertext stored as STRING is relabelled without re-encryption;
- ciphertext already stored as SECRET and values of non-secret fields are
  untouched;
- running the data step again changes nothing;
- a value it cannot handle stops the data step without rewriting anything.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from src.core.security import decrypt_secret, encrypt_secret
from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _assert_safe_database_name,
    _create_database,
    _direct_database_url,
    _drop_database,
    _run_in_database,
    _upgrade,
)

pytestmark = pytest.mark.e2e

LEGACY_REVISION = "20261116_organization_tags"
MIGRATION = Path("/app/alembic/versions/20261117_integration_secret_config_encrypt.py")


async def _seed(database_url: str, rows: dict[str, tuple[str, object, str]]) -> str:
    """Seed one integration; ``rows`` maps config id -> (schema type, value, config_type)."""
    integration_id = str(uuid4())

    async def seed(connection: AsyncConnection) -> None:
        await connection.execute(
            sa.text(
                "INSERT INTO integrations (id, name, is_deleted, created_at, updated_at) "
                "VALUES (CAST(:id AS uuid), :name, FALSE, now(), now())"
            ),
            {"id": integration_id, "name": f"rehearsal-{integration_id[:8]}"},
        )
        for config_id, (schema_type, value, config_type) in rows.items():
            schema_id = str(uuid4())
            key = f"k_{config_id[:8]}"
            await connection.execute(
                sa.text(
                    "INSERT INTO integration_config_schema "
                    "(id, integration_id, key, type, required, position, created_at, updated_at) "
                    "VALUES (CAST(:id AS uuid), CAST(:integration_id AS uuid), :key, :type, "
                    "FALSE, 0, now(), now())"
                ),
                {"id": schema_id, "integration_id": integration_id, "key": key, "type": schema_type},
            )
            await connection.execute(
                sa.text(
                    "INSERT INTO configs (id, key, value, config_type, required, position, "
                    "integration_id, config_schema_id, created_at, updated_at, updated_by) "
                    "VALUES (CAST(:id AS uuid), :key, CAST(:value AS jsonb), "
                    "CAST(:config_type AS config_type), FALSE, 0, CAST(:integration_id AS uuid), "
                    "CAST(:schema_id AS uuid), now(), now(), 'rehearsal')"
                ),
                {
                    "id": config_id,
                    "key": key,
                    "value": json.dumps({"value": value}),
                    "config_type": config_type,
                    "integration_id": integration_id,
                    "schema_id": schema_id,
                },
            )

    await _run_in_database(database_url, seed)
    return integration_id


async def _read(database_url: str) -> dict[str, tuple[Any, str]]:
    async def read(connection: AsyncConnection) -> dict[str, tuple[Any, str]]:
        result = await connection.execute(
            sa.text("SELECT id, value, CAST(config_type AS text) FROM configs")
        )
        return {str(row[0]): (row[1]["value"], row[2]) for row in result}

    return await _run_in_database(database_url, read)


async def _rerun_data_step(database_url: str) -> None:
    spec = importlib.util.spec_from_file_location("integration_secret_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    async def rerun(connection: AsyncConnection) -> None:
        await connection.run_sync(migration.encrypt_schema_secret_config_values)

    await _run_in_database(database_url, rerun)


def test_upgrade_encrypts_secret_field_values_and_fails_loudly() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    plain_string, plain_secret, cipher_string, cipher_secret, empty, other = (
        str(uuid4()) for _ in range(6)
    )
    existing_cipher = encrypt_secret("already-encrypted")
    relabel_cipher = encrypt_secret("labelled-string")

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, LEGACY_REVISION)
        asyncio.run(_seed(database_url, {
            plain_string: ("secret", "plain-as-string", "string"),
            plain_secret: ("secret", "plain-as-secret", "secret"),
            cipher_string: ("secret", relabel_cipher, "string"),
            cipher_secret: ("secret", existing_cipher, "secret"),
            empty: ("secret", "", "string"),
            other: ("string", "region-eu", "string"),
        }))

        _upgrade(database_url, "head")
        migrated = asyncio.run(_read(database_url))

        assert "plain-as-string" not in json.dumps(migrated)
        assert "plain-as-secret" not in json.dumps(migrated)
        assert migrated[plain_string][1] == "secret"
        assert decrypt_secret(migrated[plain_string][0]) == "plain-as-string"
        assert migrated[plain_secret][1] == "secret"
        assert decrypt_secret(migrated[plain_secret][0]) == "plain-as-secret"
        assert migrated[cipher_string] == (relabel_cipher, "secret")
        assert migrated[cipher_secret] == (existing_cipher, "secret")
        assert migrated[empty] == ("", "secret")
        assert migrated[other] == ("region-eu", "string")

        asyncio.run(_rerun_data_step(database_url))
        assert asyncio.run(_read(database_url)) == migrated

        # A non-text value of a secret field stops the data step; the
        # transaction rolls back, so nothing is rewritten.
        bad = str(uuid4())
        asyncio.run(_seed(database_url, {bad: ("secret", 1234, "int")}))
        before = asyncio.run(_read(database_url))
        with pytest.raises(RuntimeError, match=bad):
            asyncio.run(_rerun_data_step(database_url))
        assert asyncio.run(_read(database_url)) == before
    finally:
        asyncio.run(_drop_database(database_name))
