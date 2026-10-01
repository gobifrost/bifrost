"""True pre-head rehearsal for ``alembic/versions/20261001_webhook_secret_encrypt.py``.

Builds a disposable database at the migration's ``down_revision``, seeds
webhook sources in the pre-migration shape (plaintext secret in both config
and state, in config only, and none), upgrades to head, and checks that:

- no plaintext secret remains in config or state;
- a source that verified before keeps verifying with the same secret;
- a config-only secret, which was never enforced, is dropped, not enforced;
- running the data step again changes nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from src.services.webhooks.adapters.generic import GenericWebhookAdapter
from src.services.webhooks.protocol import Deliver, Rejected, WebhookRequest
from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _assert_safe_database_name,
    _create_database,
    _direct_database_url,
    _drop_database,
    _run_in_database,
    _upgrade,
)

pytestmark = pytest.mark.e2e

LEGACY_REVISION = "20260929_user_base_perm_fix"
MIGRATION = Path("/app/alembic/versions/20261001_webhook_secret_encrypt.py")


async def _seed(database_url: str, rows: dict[str, tuple[dict, dict]]) -> None:
    async def seed(connection: AsyncConnection) -> None:
        for source_id, (config, state) in rows.items():
            await connection.execute(
                sa.text(
                    "INSERT INTO event_sources (id, name, source_type, is_active, created_by) "
                    "VALUES (CAST(:id AS uuid), :name, 'webhook', TRUE, 'rehearsal')"
                ),
                {"id": source_id, "name": f"hook-{source_id[:8]}"},
            )
            await connection.execute(
                sa.text(
                    "INSERT INTO webhook_sources (id, event_source_id, adapter_name, config, state) "
                    "VALUES (gen_random_uuid(), CAST(:id AS uuid), 'generic', "
                    "CAST(:config AS jsonb), CAST(:state AS jsonb))"
                ),
                {"id": source_id, "config": json.dumps(config), "state": json.dumps(state)},
            )

    await _run_in_database(database_url, seed)


async def _read(database_url: str) -> dict[str, tuple[dict, dict]]:
    async def read(connection: AsyncConnection) -> dict[str, tuple[dict, dict]]:
        result = await connection.execute(
            sa.text("SELECT event_source_id, config, state FROM webhook_sources")
        )
        return {str(row[0]): (row[1], row[2]) for row in result}

    return await _run_in_database(database_url, read)


async def _rerun_data_step(database_url: str) -> None:
    spec = importlib.util.spec_from_file_location("webhook_secret_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    async def rerun(connection: AsyncConnection) -> None:
        await connection.run_sync(migration.encrypt_plaintext_webhook_secrets)

    await _run_in_database(database_url, rerun)


def _verify(state: dict, secret: str | None) -> object:
    body = b'{"event": "rehearsal"}'
    headers = {}
    if secret is not None:
        headers["x-signature-256"] = (
            "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        )
    request = WebhookRequest(
        method="POST", path="/api/hooks/x", headers=headers, query_params={}, body=body
    )
    return asyncio.run(GenericWebhookAdapter().handle_request(request, {}, state))


def test_upgrade_encrypts_enforced_secrets_and_is_idempotent() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    enforced, config_only, unsigned = str(uuid4()), str(uuid4()), str(uuid4())
    secret = f"pre-migration-{uuid4().hex}"
    ignored = f"never-enforced-{uuid4().hex}"

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, LEGACY_REVISION)
        asyncio.run(_seed(database_url, {
            enforced: (
                {"secret": secret, "signature_header": "X-Signature-256"},
                {"secret": secret},
            ),
            config_only: ({"secret": ignored}, {}),
            unsigned: ({"event_type_field": "event"}, {}),
        }))

        _upgrade(database_url, "head")
        migrated = asyncio.run(_read(database_url))

        assert secret not in json.dumps(migrated)
        assert ignored not in json.dumps(migrated)
        config, state = migrated[enforced]
        assert config == {"signature_header": "X-Signature-256"}
        assert set(state) == {"secret_encrypted"}
        assert isinstance(_verify(state, secret), Deliver)
        assert isinstance(_verify(state, None), Rejected)
        assert migrated[config_only] == ({}, {})
        assert isinstance(_verify(migrated[config_only][1], None), Deliver)
        assert migrated[unsigned] == ({"event_type_field": "event"}, {})

        asyncio.run(_rerun_data_step(database_url))
        assert asyncio.run(_read(database_url)) == migrated
    finally:
        asyncio.run(_drop_database(database_name))
