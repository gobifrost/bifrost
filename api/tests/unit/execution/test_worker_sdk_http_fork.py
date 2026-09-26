"""Gate A/C1/C2/C3a: real forked children reach the worker socket.

This is the end-to-end proof behind Gate A and the config, integrations, and
table slices of Gate C. A real ``TemplateProcess`` forks a one-shot child and
injects the worker's Unix socket path exactly as the pool does. The test
process serves the **real** SDK routes on that socket via uvicorn, against the
real database engine. The child's network API is dead by environment, and it
receives no database or provider credentials, so a correct value proves:

- the existing routes are reused (not copied) and resolve the value;
- the child used the socket transport, not the network API (zero
  API-container requests: ``BIFROST_API_URL`` points at a dead port);
- the parent owns DB access; the child holds no DB credential.

Marked ``slow`` like the other real-fork tests: template boot costs seconds.
"""

import asyncio
import base64
import contextlib
import os
import time
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio

from src.services.execution.template_process import TemplateProcess
from src.services.execution.worker_sdk_http import WorkerSdkHttpServer

pytestmark = pytest.mark.slow


def _no_cache():
    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()
    return patch(
        "src.core.cache.redis_client.get_shared_redis",
        new=AsyncMock(return_value=redis),
    )


@pytest_asyncio.fixture
async def committed_config(async_session_factory):
    """Seed a globally-scoped config row in a committed session."""
    from sqlalchemy import delete

    from src.models.enums import ConfigType as ConfigTypeEnum
    from src.models.orm.config import Config as ConfigModel

    created: list[str] = []

    async def _seed(key: str, value: object) -> None:
        async with async_session_factory() as session:
            session.add(
                ConfigModel(
                    key=key,
                    value={"value": value},
                    config_type=ConfigTypeEnum("string"),
                    organization_id=None,
                    updated_by="worker-sdk-http-fork-test",
                )
            )
            await session.commit()
        created.append(key)

    yield _seed

    async with async_session_factory() as session:
        for key in created:
            await session.execute(
                delete(ConfigModel).where(
                    ConfigModel.key == key,
                    ConfigModel.organization_id.is_(None),
                )
            )
        await session.commit()


def _script_for(key: str) -> str:
    source = (
        "import os, sys\n"
        "from bifrost import config\n"
        "from bifrost.client import get_engine_socket_path\n"
        f"await config.set({key!r}, 'fork-set-value')\n"
        f"_get = await config.get({key!r})\n"
        "_list = await config.list()\n"
        f"_deleted = await config.delete({key!r})\n"
        f"_after = await config.get({key!r}, default='missing')\n"
        "result = {\n"
        "    'value': _get,\n"
        f"    'listed': _list.data.get({key!r}),\n"
        "    'deleted': _deleted,\n"
        "    'after_delete': _after,\n"
        "    'socket_path': get_engine_socket_path(),\n"
        "    'had_db_url': (\n"
        "        'BIFROST_DATABASE_URL' in os.environ\n"
        "        or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
        "    ),\n"
        "    'had_sqlalchemy': 'sqlalchemy' in sys.modules,\n"
        "}\n"
    )
    return base64.b64encode(source.encode("utf-8")).decode("utf-8")


def _context_for(
    code_b64: str,
    engine_token: str,
    organization: dict[str, Any] | None = None,
    is_platform_admin: bool = True,
) -> dict[str, Any]:
    return {
        "execution_id": f"wsdk-fork-{uuid4().hex[:8]}",
        "name": "worker-sdk-http-fork-test",
        "code": code_b64,
        "parameters": {},
        "caller": {
            "user_id": "00000000-0000-0000-0000-000000000001",
            "email": "engine@bifrost.internal",
            "name": "Bifrost Engine",
        },
        "organization": organization,
        "tags": [],
        "timeout_seconds": 120,
        "cache_ttl_seconds": 0,
        "transient": True,
        "no_cache": True,
        "is_platform_admin": is_platform_admin,
        "engine_token": engine_token,
    }


def _wait_for_pid_to_die(pid: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except OSError:
            return


@pytest.mark.asyncio
async def test_forked_child_config_crud_over_worker_socket(
    committed_config, monkeypatch
):
    from src.core.security import mint_engine_token

    key = f"wsdk-fork-{uuid4().hex[:8]}"
    await committed_config(key, "fork-socket-value")

    # Any attempt to reach the network API fails loudly; the socket must
    # serve the call.
    monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

    engine_token, _ = mint_engine_token(
        execution_id="gate-a-fork",
        solution_id=None,
        global_repo_access=True,
        timeout_seconds=120,
    )

    server = WorkerSdkHttpServer()
    await server.start()
    assert server.socket_path is not None

    template = TemplateProcess()
    template.start()
    try:
        with _no_cache():
            child_pid, work_queue, result_queue = template.fork(
                worker_id="wsdk-socket-fork",
                sdk_socket_path=server.socket_path,
            )
            try:
                work_queue.put(
                    (
                        "exec-wsdk-socket",
                        _context_for(_script_for(key), engine_token),
                    )
                )
                envelope = await asyncio.to_thread(result_queue.get, True, 90.0)
            finally:
                work_queue.close()
                result_queue.close()

        assert envelope["success"] is True, envelope
        result = envelope["result"]
        assert result["value"] == "fork-set-value"
        assert result["listed"] == "fork-set-value"
        assert result["deleted"] is True
        assert result["after_delete"] == "missing"
        assert result["socket_path"] == server.socket_path
        assert result["had_db_url"] is False
        assert result["had_sqlalchemy"] is False

        _wait_for_pid_to_die(child_pid)
    finally:
        with contextlib.suppress(Exception):
            template.shutdown()
        await server.stop()


def _refresh_outcome(access: str) -> dict[str, Any]:
    """A successful provider-refresh outcome, as the shared primitive returns."""
    from datetime import datetime, timezone

    from src.core.security import encrypt_secret

    return {
        "success": True,
        "access_token": access,
        "encrypted_access_token": encrypt_secret(access).encode(),
        "encrypted_refresh_token": encrypt_secret("fresh-refresh").encode(),
        "refresh_token": "fresh-refresh",
        "expires_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
    }


async def _seed_committed_integration(async_session_factory) -> dict[str, Any]:
    """Seed a committed org-bound integration + mapping + global OAuth provider.

    The worker socket opens its own session from the worker's global engine,
    so every row must be committed (not the rolled-back ``db_session``). The
    provider is global and ``client_credentials``: the engine sentinel caller
    has no org, so ``refresh()`` (which passes no scope) resolves to the
    global tier.
    """
    from src.core.security import encrypt_secret
    from src.models.enums import ConfigType as ConfigTypeEnum
    from src.models.orm import Config as ConfigModel
    from src.models.orm.integrations import (
        Integration as IntegrationModel,
    )
    from src.models.orm.integrations import (
        IntegrationConfigSchema as SchemaModel,
    )
    from src.models.orm.integrations import (
        IntegrationMapping as MappingModel,
    )
    from src.models.orm.oauth import OAuthProvider
    from src.models.orm.organizations import Organization as OrganizationModel

    tag = uuid4().hex[:8]
    async with async_session_factory() as session:
        org = OrganizationModel(
            name=f"wsdk-fork-org-{tag}",
            is_active=True,
            is_provider=False,
            created_by="worker-sdk-http-fork-test",
        )
        other = OrganizationModel(
            name=f"wsdk-fork-other-{tag}",
            is_active=True,
            is_provider=False,
            created_by="worker-sdk-http-fork-test",
        )
        session.add_all([org, other])
        await session.flush()
        org_id = org.id
        other_id = other.id

        name = f"wsdk-fork-integ-{tag}"
        integration = IntegrationModel(name=name)
        session.add(integration)
        await session.flush()
        integration_id = integration.id

        entity = f"fork-entity-{tag}"
        session.add(
            SchemaModel(
                integration_id=integration.id,
                key="api_key",
                type="secret",
                position=0,
            )
        )
        session.add_all(
            [
                ConfigModel(
                    key="region",
                    value={"value": "us-default"},
                    config_type=ConfigTypeEnum.STRING,
                    organization_id=None,
                    integration_id=integration.id,
                    updated_by="worker-sdk-http-fork-test",
                ),
                ConfigModel(
                    key="api_key",
                    value={"value": encrypt_secret("global-secret-canary")},
                    config_type=ConfigTypeEnum.SECRET,
                    organization_id=None,
                    integration_id=integration.id,
                    updated_by="worker-sdk-http-fork-test",
                ),
                ConfigModel(
                    key="region",
                    value={"value": "eu-fork"},
                    config_type=ConfigTypeEnum.STRING,
                    organization_id=org.id,
                    integration_id=integration.id,
                    updated_by="worker-sdk-http-fork-test",
                ),
                MappingModel(
                    integration_id=integration.id,
                    organization_id=org.id,
                    entity_id=entity,
                    entity_name=f"entity-{tag}",
                ),
            ]
        )

        provider_name = f"wsdk-fork-prov-{tag}"
        session.add(
            OAuthProvider(
                provider_name=provider_name,
                client_id="fork-client",
                encrypted_client_secret=encrypt_secret("fork-secret").encode(),
                oauth_flow_type="client_credentials",
                token_url="https://example.com/token",
                token_url_defaults={},
                scopes=["read"],
                integration_id=integration.id,
                organization_id=None,
            )
        )
        await session.commit()

    return {
        "org_id": str(org_id),
        "other_org_id": str(other_id),
        "integration_id": integration_id,
        "name": name,
        "entity": entity,
        "provider_name": provider_name,
        "missing_provider": f"wsdk-fork-missing-{tag}",
    }


async def _cleanup_committed_integration(async_session_factory, seed) -> None:
    from sqlalchemy import delete, or_, select

    from src.models.orm import Config as ConfigModel
    from src.models.orm.integrations import (
        Integration as IntegrationModel,
    )
    from src.models.orm.integrations import (
        IntegrationConfigSchema as SchemaModel,
    )
    from src.models.orm.integrations import (
        IntegrationMapping as MappingModel,
    )
    from src.models.orm.oauth import OAuthProvider, OAuthToken
    from src.models.orm.organizations import Organization as OrganizationModel

    integration_id = seed["integration_id"]
    org_ids = [seed["org_id"], seed["other_org_id"]]
    async with async_session_factory() as session:
        provider_ids = (
            await session.execute(
                select(OAuthProvider.id).where(
                    OAuthProvider.integration_id == integration_id
                )
            )
        ).scalars().all()
        if provider_ids:
            await session.execute(
                delete(OAuthToken).where(
                    OAuthToken.provider_id.in_(provider_ids)
                )
            )
        await session.execute(
            delete(OAuthProvider).where(
                OAuthProvider.integration_id == integration_id
            )
        )
        await session.execute(
            delete(ConfigModel).where(
                or_(
                    ConfigModel.integration_id == integration_id,
                    ConfigModel.organization_id.in_(org_ids),
                )
            )
        )
        await session.execute(
            delete(MappingModel).where(
                MappingModel.integration_id == integration_id
            )
        )
        await session.execute(
            delete(SchemaModel).where(
                SchemaModel.integration_id == integration_id
            )
        )
        await session.execute(
            delete(IntegrationModel).where(IntegrationModel.id == integration_id)
        )
        await session.execute(
            delete(OrganizationModel).where(OrganizationModel.id.in_(org_ids))
        )
        await session.commit()


def _integrations_script(
    *,
    name: str,
    org: str,
    other_org: str,
    entity: str,
    provider: str,
    missing_provider: str,
    access: str,
) -> str:
    source = f'''import os, sys
from bifrost import integrations
from bifrost.models import OAuthCredentials
from bifrost._context import get_execution_context
from bifrost.client import get_engine_socket_path

data = await integrations.get({name!r})
mappings = await integrations.list_mappings({name!r})
mapping = await integrations.get_mapping({name!r})

creds = OAuthCredentials(
    connection_name={provider!r}, client_id=None, client_secret=None,
    authorization_url=None, token_url=None, scopes=[],
    access_token=None, refresh_token=None, expires_at=None,
)
await creds.refresh()
_registered = get_execution_context()._collect_secret_values()

missing = OAuthCredentials(
    connection_name={missing_provider!r}, client_id=None, client_secret=None,
    authorization_url=None, token_url=None, scopes=[],
    access_token=None, refresh_token=None, expires_at=None,
)
try:
    await missing.refresh()
    missing_out = "UNEXPECTED-SUCCESS"
except Exception as e:
    missing_out = f"{{type(e).__name__}}: {{e}}"

created = await integrations.upsert_mapping(
    {name!r}, scope={org!r}, entity_id={entity!r} + "-new",
    entity_name="Fork Entity", config={{"region": "ap-fork"}},
)
deleted = await integrations.delete_mapping({name!r}, scope={org!r})
after = await integrations.get_mapping({name!r})

try:
    await integrations.get({name!r}, scope={other_org!r})
    cross_org = "LEAKED"
except Exception as e:
    cross_org = f"denied: {{type(e).__name__}}"

result = {{
    "socket_path": get_engine_socket_path(),
    "used_socket": get_engine_socket_path() is not None,
    "had_db_url": (
        "BIFROST_DATABASE_URL" in os.environ
        or "BIFROST_DATABASE_URL_SYNC" in os.environ
    ),
    "had_sqlalchemy": "sqlalchemy" in sys.modules,
    "entity_id": data.entity_id,
    "region": data.config.get("region"),
    "secret_ok": data.config.get("api_key") == "global-secret-canary",
    "secret_keys": data.config_secret_keys,
    "list_count": len(mappings),
    "list_entity": mappings[0].entity_id,
    "mapping_entity": mapping.entity_id,
    "refresh_ok": creds.access_token == {access!r},
    "refresh_registered": {access!r} in _registered,
    "missing_refresh": missing_out,
    "created_entity": created.entity_id,
    "created_region": created.config.get("region"),
    "deleted": deleted,
    "after": after,
    "cross_org": cross_org,
}}
'''
    return base64.b64encode(source.encode("utf-8")).decode("utf-8")


@pytest.mark.asyncio
async def test_forked_child_integrations_over_worker_socket(
    async_session_factory, monkeypatch
):
    """All six integrations methods reach the real routes over the socket.

    A real child forks with the worker's Unix socket injected and a dead
    network API. It reads (get/list_mappings/get_mapping), refreshes an OAuth
    token, mutates (upsert/delete), and probes a cross-org scope. Success with
    no DB credential proves the socket carried every call to the parent-served
    routes with the same scope, secret, and error behavior as the network API.
    """
    from src.core.security import mint_engine_token

    seed = await _seed_committed_integration(async_session_factory)
    access = "fresh-fork-access"

    # Any attempt to reach the network API fails loudly; the socket must
    # serve every call.
    monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

    engine_token, _ = mint_engine_token(
        execution_id="gate-c2-fork",
        solution_id=None,
        global_repo_access=True,
        timeout_seconds=120,
    )
    organization = {"id": seed["org_id"], "name": "Fork Org"}

    server = WorkerSdkHttpServer()
    await server.start()
    assert server.socket_path is not None

    template = TemplateProcess()
    template.start()
    try:
        with (
            patch(
                "src.services.oauth_provider.refresh_oauth_token_http",
                new=AsyncMock(return_value=_refresh_outcome(access)),
            ) as refresh_http,
        ):
            child_pid, work_queue, result_queue = template.fork(
                worker_id="wsdk-integ-fork",
                sdk_socket_path=server.socket_path,
            )
            try:
                work_queue.put(
                    (
                        "exec-wsdk-integ",
                        _context_for(
                            _integrations_script(
                                name=seed["name"],
                                org=seed["org_id"],
                                other_org=seed["other_org_id"],
                                entity=seed["entity"],
                                provider=seed["provider_name"],
                                missing_provider=seed["missing_provider"],
                                access=access,
                            ),
                            engine_token,
                            organization,
                            is_platform_admin=False,
                        ),
                    )
                )
                envelope = await asyncio.to_thread(result_queue.get, True, 90.0)
            finally:
                work_queue.close()
                result_queue.close()

        assert envelope["success"] is True, envelope
        result = envelope["result"]
        # Transport proof: socket injected, network API dead, no DB credential.
        assert result["used_socket"] is True
        assert result["socket_path"] == server.socket_path
        assert result["had_db_url"] is False
        assert result["had_sqlalchemy"] is False
        # Reads carry the org mapping, merged config, and decrypted secret.
        assert result["entity_id"] == seed["entity"]
        assert result["region"] == "eu-fork"
        assert result["secret_ok"] is True
        assert result["secret_keys"] == ["api_key"]
        assert result["list_count"] == 1
        assert result["list_entity"] == seed["entity"]
        assert result["mapping_entity"] == seed["entity"]
        # OAuth refresh persisted a fresh token and registered it as a secret.
        assert result["refresh_ok"] is True
        assert result["refresh_registered"] is True
        refresh_http.assert_awaited_once()
        # Error mapping: a missing provider surfaces the HTTP-shaped failure.
        assert str(result["missing_refresh"]).startswith(
            "RuntimeError: Token refresh failed: 404"
        ), result
        # Mutations: upsert echoes the merged write, delete removes it.
        assert result["created_entity"] == seed["entity"] + "-new"
        assert result["created_region"] == "ap-fork"
        assert result["deleted"] is True
        assert result["after"] is None
        # Scope: a cross-org override by a non-bypass caller is denied.
        assert str(result["cross_org"]).startswith("denied"), result

        _wait_for_pid_to_die(child_pid)
    finally:
        with contextlib.suppress(Exception):
            template.shutdown()
        await server.stop()
        await _cleanup_committed_integration(async_session_factory, seed)


async def _seed_committed_org(async_session_factory) -> str:
    """Seed a committed organization the forked child scopes tables to.

    The worker socket opens its own session from the worker's global engine,
    so the org must be committed (not the rolled-back ``db_session``).
    """
    from src.models.orm.organizations import Organization as OrganizationModel

    async with async_session_factory() as session:
        org = OrganizationModel(
            name=f"wsdk-fork-tables-org-{uuid4().hex[:8]}",
            is_active=True,
            is_provider=False,
            created_by="worker-sdk-http-fork-test",
        )
        session.add(org)
        await session.commit()
        org_id = str(org.id)
    return org_id


async def _cleanup_committed_org(async_session_factory, org_id: str) -> None:
    from sqlalchemy import delete

    from src.models.orm.organizations import Organization as OrganizationModel

    async with async_session_factory() as session:
        await session.execute(
            delete(OrganizationModel).where(OrganizationModel.id == org_id)
        )
        await session.commit()


def _table_script(
    *, scope: str, table_name: str, missing_name: str, auto_name: str
) -> str:
    source = f'''import os, sys
from bifrost import tables
from bifrost.client import get_engine_socket_path

_socket = get_engine_socket_path()

_created = await tables.create({table_name!r}, description="fork-table")
_listed_names = [t.name for t in await tables.list(scope={scope!r})]
_listed_scope = [t.name for t in await tables.list()]
_dup = ""
try:
    await tables.create({table_name!r})
    _dup = "UNEXPECTED-SUCCESS"
except Exception as e:
    _dup = f"{{type(e).__name__}}:{{e.response.status_code}}"

from bifrost import _local_transport as _lt
_transport = _lt.get()
_installed = _transport is not None
_channel = "installed" if _installed else "absent"

_missing_query = await tables.query({missing_name!r}, scope={scope!r})
_missing_count = await tables.count({missing_name!r}, scope={scope!r})

# Single-document writes.
_inserted = await tables.insert({table_name!r}, {{"v": 1}}, id="ins-1", scope={scope!r})
_upserted = await tables.upsert({table_name!r}, "ups-1", {{"v": 2}}, scope={scope!r})
_updated = await tables.update({table_name!r}, "ups-1", {{"v": 3}}, scope={scope!r})
_update_missing = await tables.update({table_name!r}, "nope", {{"v": 0}}, scope={scope!r})
_delete_missing = await tables.delete_document({table_name!r}, "nope", scope={scope!r})
_delete_hit = await tables.delete_document({table_name!r}, "ins-1", scope={scope!r})

# Batch writes.
_insert_batch = await tables.insert_batch(
    {table_name!r},
    [{{"id": "b1", "data": {{"v": 1}}}}, {{"data": {{"v": 2}}}}],
    scope={scope!r},
)
_upsert_batch = await tables.upsert_batch(
    {table_name!r}, [{{"id": "b1", "data": {{"v": 9}}}}], scope={scope!r},
)
_bulk = await tables.bulk_upsert(
    {table_name!r},
    [{{"id": "u1", "data": {{"v": 5}}}}, {{"id": "u2", "data": {{"v": 6}}}}],
    scope={scope!r},
)
_delete_batch = await tables.delete_batch(
    {table_name!r}, ["u1", "ghost"], scope={scope!r},
)
_delete_batch_missing = await tables.delete_batch(
    {missing_name!r}, ["x"], scope={scope!r},
)

# Auto-create-on-insert helper creates the table then retries the write.
_auto_doc = await tables.insert({auto_name!r}, {{"v": 7}}, id="auto-1", scope={scope!r})
_auto_query = await tables.query({auto_name!r}, scope={scope!r})

_written_count = await tables.count({table_name!r}, scope={scope!r})

# A realistic 24 MiB batch: one request over the same socket, with no
# base64 channel wrapper and no frame-size ceiling.
import json as _json
_blob = "x" * (1024 * 1024)
_large_docs = [{{"id": f"big-{{i}}", "data": {{"blob": _blob}}}} for i in range(24)]
_large_request_bytes = len(_json.dumps(_large_docs))
_large_result = await tables.bulk_upsert({table_name!r}, _large_docs, scope={scope!r})
_large_count = await tables.count({table_name!r}, scope={scope!r})

_deleted = await tables.delete(_created.id)
_auto_deleted = await tables.delete(_auto_doc.table_id)
_after_delete = await tables.list(scope={scope!r})

result = {{
    "used_socket": _socket is not None,
    "socket_path": _socket,
    "had_db_url": (
        "BIFROST_DATABASE_URL" in os.environ
        or "BIFROST_DATABASE_URL_SYNC" in os.environ
    ),
    "had_sqlalchemy": "sqlalchemy" in sys.modules,
    "created_id": _created.id,
    "created_name": _created.name,
    "created_scope": _created.organization_id,
    "listed_names": _listed_names,
    "listed_default_scope": _listed_scope,
    "dup": _dup,
    "channel_installed": _installed,
    "channel_get": _channel,
    "missing_total": _missing_query.total,
    "missing_docs": _missing_query.documents,
    "missing_count": _missing_count,
    "inserted_id": _inserted.id,
    "inserted_data": _inserted.data,
    "upserted_id": _upserted.id,
    "updated_data": _updated.data,
    "update_missing": _update_missing,
    "delete_missing": _delete_missing,
    "delete_hit": _delete_hit,
    "insert_batch_count": _insert_batch.count,
    "insert_batch_docs": len(_insert_batch.documents),
    "upsert_batch_count": _upsert_batch.count,
    "bulk_count": _bulk.count,
    "delete_batch_ids": _delete_batch.deleted_ids,
    "delete_batch_count": _delete_batch.count,
    "delete_batch_missing_ids": _delete_batch_missing.deleted_ids,
    "delete_batch_missing_count": _delete_batch_missing.count,
    "auto_doc_id": _auto_doc.id,
    "auto_total": _auto_query.total,
    "written_count": _written_count,
    "large_request_bytes": _large_request_bytes,
    "large_result_count": _large_result.count,
    "large_count": _large_count,
    "deleted": _deleted,
    "auto_deleted": _auto_deleted,
    "after_delete": _after_delete,
}}
'''
    return base64.b64encode(source.encode("utf-8")).decode("utf-8")


@pytest.mark.asyncio
async def test_forked_child_tables_over_worker_socket(
    async_session_factory, monkeypatch
):
    """Gate C3a/C3b: all table routes reach the real routes over the socket.

    A real child forks with the worker's Unix socket injected and a dead
    network API. It creates/lists/deletes table definitions, reads
    documents, performs every single/batch write (including auto-create),
    and pushes a realistic ~24 MiB batch. This fork installs only the worker
    socket, not the dedicated channel. Success with no DB credential proves
    the shared client carried every call to the parent-served routes over
    the socket, with the same scope, 404, retry, and delete semantics as the
    network API.
    """
    from src.core.security import mint_engine_token

    org_id = await _seed_committed_org(async_session_factory)
    tag = uuid4().hex[:8]
    table_name = f"forktbl_{tag}"
    missing_name = f"forkmissing_{tag}"
    auto_name = f"forkauto_{tag}"

    # Any attempt to reach the network API fails loudly; the socket must
    # serve every call.
    monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

    engine_token, _ = mint_engine_token(
        execution_id="gate-c3a-fork",
        solution_id=None,
        global_repo_access=True,
        timeout_seconds=120,
    )
    organization = {"id": org_id, "name": "Fork Tables Org"}

    server = WorkerSdkHttpServer()
    await server.start()
    assert server.socket_path is not None

    template = TemplateProcess()
    template.start()
    try:
        child_pid, work_queue, result_queue = template.fork(
            worker_id="wsdk-tables-fork",
            sdk_socket_path=server.socket_path,
        )
        try:
            work_queue.put(
                (
                    "exec-wsdk-tables",
                    _context_for(
                        _table_script(
                            scope=org_id,
                            table_name=table_name,
                            missing_name=missing_name,
                            auto_name=auto_name,
                        ),
                        engine_token,
                        organization,
                        is_platform_admin=False,
                    ),
                )
            )
            envelope = await asyncio.to_thread(result_queue.get, True, 90.0)
        finally:
            work_queue.close()
            result_queue.close()

        assert envelope["success"] is True, envelope
        result = envelope["result"]
        # Transport proof: socket injected, network API dead, no DB credential.
        assert result["used_socket"] is True
        assert result["socket_path"] == server.socket_path
        assert result["had_db_url"] is False
        assert result["had_sqlalchemy"] is False
        # Definitions: create persists the requested org scope, list sees it,
        # a duplicate is a 409.
        assert result["created_id"]
        assert result["created_name"] == table_name
        assert result["created_scope"] == org_id
        assert result["listed_names"] == [table_name]
        assert result["listed_default_scope"] == [table_name]
        assert result["dup"].startswith("BifrostAPIError:409"), result
        # This fork injects only the worker socket, not the dedicated
        # channel: the migrated table operations ride the socket rather
        # than the old pipe.
        assert result["channel_installed"] is False
        assert result["channel_get"] == "absent"
        # Reads: a missing table maps to an empty query and a zero count on
        # both transports (the SDK-level 404 mapping), matching the network
        # API contract rather than surfacing the route's 404.
        assert result["missing_total"] == 0
        assert result["missing_docs"] == []
        assert result["missing_count"] == 0
        # Single-document writes: insert/upsert/update/delete round-trip the
        # same results as the HTTP routes, and a missing row maps to
        # update→None / delete→False.
        assert result["inserted_id"] == "ins-1"
        assert result["inserted_data"] == {"v": 1}
        assert result["upserted_id"] == "ups-1"
        assert result["updated_data"] == {"v": 3}
        assert result["update_missing"] is None
        assert result["delete_missing"] is False
        assert result["delete_hit"] is True
        # Batch writes: counts, generated ids, and missing-id skipping.
        assert result["insert_batch_count"] == 2
        assert result["insert_batch_docs"] == 2
        assert result["upsert_batch_count"] == 1
        assert result["bulk_count"] == 2
        assert result["delete_batch_ids"] == ["u1"]
        assert result["delete_batch_count"] == 1
        assert result["delete_batch_missing_ids"] == []
        assert result["delete_batch_missing_count"] == 0
        assert result["written_count"] == 4
        # Auto-create-on-insert: the helper creates the missing table over
        # the socket and retries the write.
        assert result["auto_doc_id"] == "auto-1"
        assert result["auto_total"] == 1
        # ~24 MiB in one batch proves the socket path carries realistic large
        # payloads with no base64 channel wrapper.
        assert 23 * 1024 * 1024 < result["large_request_bytes"] < 26 * 1024 * 1024
        assert result["large_result_count"] == 24
        assert result["large_count"] == 28
        # Delete commits for both the definition-created and auto-created
        # tables.
        assert result["deleted"] is True
        assert result["auto_deleted"] is True
        assert result["after_delete"] == []

        _wait_for_pid_to_die(child_pid)
    finally:
        with contextlib.suppress(Exception):
            template.shutdown()
        await server.stop()
        await _cleanup_committed_org(async_session_factory, org_id)
