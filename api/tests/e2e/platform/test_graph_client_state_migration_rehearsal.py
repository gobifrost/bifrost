"""True pre-head rehearsal for ``alembic/versions/20261001_graph_client_state_encrypt.py``.

Builds a disposable database at the migration's ``down_revision``, seeds
Microsoft Graph webhook sources in the pre-migration shape (plaintext
``client_state`` in state, and none), upgrades to head, and checks that:

- no plaintext client state remains;
- a subscription that validated before keeps validating with the same value;
- other state, and rows without a client state, are untouched;
- running the data step again changes nothing.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from src.services.webhooks.adapters.microsoft_graph import CLIENT_STATE_KEY, MicrosoftGraphAdapter
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

LEGACY_REVISION = "20261001_webhook_secret_enc"
MIGRATION = Path("/app/alembic/versions/20261001_graph_client_state_encrypt.py")
CONFIG = {"resource": "/users/user-1/messages", "change_types": ["created"]}


async def _seed(database_url: str, rows: dict[str, dict]) -> None:
    async def seed(connection: AsyncConnection) -> None:
        for source_id, state in rows.items():
            await connection.execute(
                sa.text(
                    "INSERT INTO event_sources (id, name, source_type, is_active, created_by) "
                    "VALUES (CAST(:id AS uuid), :name, 'webhook', TRUE, 'rehearsal')"
                ),
                {"id": source_id, "name": f"graph-{source_id[:8]}"},
            )
            await connection.execute(
                sa.text(
                    "INSERT INTO webhook_sources (id, event_source_id, adapter_name, config, state) "
                    "VALUES (gen_random_uuid(), CAST(:id AS uuid), 'microsoft_graph', "
                    "CAST(:config AS jsonb), CAST(:state AS jsonb))"
                ),
                {"id": source_id, "config": json.dumps(CONFIG), "state": json.dumps(state)},
            )

    await _run_in_database(database_url, seed)


async def _read(database_url: str) -> dict[str, dict]:
    async def read(connection: AsyncConnection) -> dict[str, dict]:
        result = await connection.execute(
            sa.text("SELECT event_source_id, state FROM webhook_sources")
        )
        return {str(row[0]): row[1] for row in result}

    return await _run_in_database(database_url, read)


async def _rerun_data_step(database_url: str) -> None:
    spec = importlib.util.spec_from_file_location("graph_client_state_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    async def rerun(connection: AsyncConnection) -> None:
        await connection.run_sync(migration.encrypt_plaintext_graph_client_state)

    await _run_in_database(database_url, rerun)


def _notify(state: dict, client_state: str) -> object:
    request = WebhookRequest(
        method="POST",
        path="/api/hooks/x",
        headers={},
        query_params={},
        body=json.dumps(
            {"value": [{"changeType": "created", "clientState": client_state}]}
        ).encode(),
    )
    return asyncio.run(MicrosoftGraphAdapter().handle_request(request, CONFIG, state))


def test_upgrade_encrypts_client_state_and_is_idempotent() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    subscribed, bare = str(uuid4()), str(uuid4())
    client_state = f"pre-migration-{uuid4().hex}"

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, LEGACY_REVISION)
        asyncio.run(_seed(database_url, {
            subscribed: {"client_state": client_state, "user_display_name": "Ada"},
            bare: {"user_display_name": "Grace"},
        }))

        _upgrade(database_url, "head")
        migrated = asyncio.run(_read(database_url))

        assert client_state not in json.dumps(migrated)
        state = migrated[subscribed]
        assert set(state) == {CLIENT_STATE_KEY, "user_display_name"}
        assert state["user_display_name"] == "Ada"
        assert isinstance(_notify(state, client_state), Deliver)
        assert isinstance(_notify(state, "wrong"), Rejected)
        assert migrated[bare] == {"user_display_name": "Grace"}

        asyncio.run(_rerun_data_step(database_url))
        assert asyncio.run(_read(database_url)) == migrated
    finally:
        asyncio.run(_drop_database(database_name))
