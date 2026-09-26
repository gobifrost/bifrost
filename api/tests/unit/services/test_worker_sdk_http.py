"""Gate A: worker-local engine SDK over ordinary HTTP on a Unix socket.

Proves the worker can serve the **existing** SDK config routes (no copied
handlers, no full API app) on a private socket, that an engine child's
``config.get`` reaches them with the normal bearer-token auth and scope
rules, and that a failed local request never falls back to the network API.

The real forked-child round trip lives in
``tests/unit/execution/test_worker_sdk_http_fork.py``.
"""

import asyncio
import os
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from bifrost.client import (
    BifrostClient,
    _clear_client,
    _clear_engine_socket,
    _install_engine_socket,
    _set_client,
    get_engine_socket_path,
)
from src.services.execution.worker_sdk_http import (
    ARTIFACT_ROUTE_METHODS,
    ARTIFACT_ROUTE_PATHS,
    CONFIG_ROUTE_PATHS,
    FILES_ROUTE_PATHS,
    INTEGRATION_ROUTE_PATHS,
    PLATFORM_JOB_ROUTE_METHODS,
    SDK_ROUTE_PATHS,
    TABLE_ROUTE_METHODS,
    TABLE_SDK_ROUTE_PATHS,
    WorkerSdkHttpServer,
    build_worker_sdk_app,
)


@pytest.fixture(autouse=True)
def _reset_engine_transport_globals():
    """Keep the trusted-injection globals from leaking across tests."""
    yield
    _clear_engine_socket()
    _clear_client()


@pytest_asyncio.fixture
async def committed_config(async_session_factory):
    """Seed globally-scoped config rows in committed sessions.

    The socket server opens its own session from the worker's global engine,
    so seeded rows must be committed (not the rolled-back ``db_session``).
    """
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
                    updated_by="worker-sdk-http-test",
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


@dataclass
class _EngineUser:
    """Minimal superuser principal matching an engine token's claims."""

    organization_id: None = None
    is_superuser: bool = True
    is_external: bool = False
    email: str = "engine@bifrost.internal"


def _no_cache():
    """Force the config repository past its Redis cache to the DB query.

    The repository caches the whole global config hash on first read, so a
    row committed after another test's read would be invisible without this.
    """
    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()
    return patch(
        "src.core.cache.redis_client.get_shared_redis",
        new=AsyncMock(return_value=redis),
    )


def _engine_token() -> str:
    from src.core.security import mint_engine_token

    token, _ = mint_engine_token(
        execution_id="gate-a-route-reuse",
        solution_id=None,
        global_repo_access=True,
        timeout_seconds=300,
    )
    return token


def _socket_client(server: WorkerSdkHttpServer) -> httpx.AsyncClient:
    assert server.socket_path is not None
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=server.socket_path),
        base_url="http://bifrost-engine",
    )


class TestRouteReuse:
    """The worker app mounts the real router's route objects unchanged."""

    def test_build_app_mounts_real_sdk_routes_by_identity(self):
        from fastapi.routing import APIRoute

        from src.routers.cli import router as sdk_router
        from src.routers.files import router as files_router
        from src.routers.platform_jobs import router as platform_jobs_router
        from src.routers.tables import router as tables_router

        cli_path_only = (
            CONFIG_ROUTE_PATHS | INTEGRATION_ROUTE_PATHS | TABLE_SDK_ROUTE_PATHS
        )
        cli_originals = {
            route.path: route
            for route in sdk_router.routes
            if getattr(route, "path", None) in cli_path_only
        }
        assert set(cli_originals) == cli_path_only

        artifact_originals = {
            (route.path, method): route
            for route in sdk_router.routes
            if getattr(route, "path", None) in ARTIFACT_ROUTE_METHODS
            for method in (getattr(route, "methods", None) or set())
            & ARTIFACT_ROUTE_METHODS[route.path]
        }
        assert set(artifact_originals) == {
            (path, method)
            for path, methods in ARTIFACT_ROUTE_METHODS.items()
            for method in methods
        }

        files_originals = {
            route.path: route
            for route in files_router.routes
            if getattr(route, "path", None) in FILES_ROUTE_PATHS
        }
        assert set(files_originals) == FILES_ROUTE_PATHS

        tables_originals = {
            (route.path, method): route
            for route in tables_router.routes
            if (wanted := TABLE_ROUTE_METHODS.get(getattr(route, "path", None)))
            for method in (getattr(route, "methods", None) or set()) & wanted
        }

        platform_job_originals = {
            (route.path, method): route
            for route in platform_jobs_router.routes
            if (wanted := PLATFORM_JOB_ROUTE_METHODS.get(getattr(route, "path", None)))
            for method in (getattr(route, "methods", None) or set()) & wanted
        }

        app = build_worker_sdk_app()
        mounted = [route for route in app.router.routes if isinstance(route, APIRoute)]
        mounted_cli = {
            route.path: route for route in mounted if route.path in cli_path_only
        }
        mounted_artifacts = {
            (route.path, method): route
            for route in mounted
            if route.path in ARTIFACT_ROUTE_METHODS
            for method in route.methods
        }
        mounted_files = {
            route.path: route for route in mounted if route.path in FILES_ROUTE_PATHS
        }
        mounted_tables = {
            (route.path, method): route
            for route in mounted
            if route.path in TABLE_ROUTE_METHODS
            for method in route.methods
        }
        mounted_platform_jobs = {
            (route.path, method): route
            for route in mounted
            if route.path in PLATFORM_JOB_ROUTE_METHODS
            for method in route.methods
        }

        # Only the selected routes, and the exact registered objects — no
        # copied handlers and no rest of the API surface.
        assert set(mounted_cli) == cli_path_only
        for path, route in mounted_cli.items():
            assert route is cli_originals[path]
            assert route.endpoint is cli_originals[path].endpoint

        assert set(mounted_artifacts) == set(artifact_originals)
        for key, route in mounted_artifacts.items():
            assert route is artifact_originals[key]
            assert route.endpoint is artifact_originals[key].endpoint

        assert set(mounted_files) == FILES_ROUTE_PATHS
        for path, route in mounted_files.items():
            assert route is files_originals[path]
            assert route.endpoint is files_originals[path].endpoint

        assert set(mounted_tables) == set(tables_originals)
        for key, route in mounted_tables.items():
            assert route is tables_originals[key]
            assert route.endpoint is tables_originals[key].endpoint

        assert set(mounted_platform_jobs) == set(platform_job_originals)
        for key, route in mounted_platform_jobs.items():
            assert route is platform_job_originals[key]
            assert route.endpoint is platform_job_originals[key].endpoint

        # The shared ``/api/tables/{table_id}`` path must not drag in its
        # GET/PATCH metadata siblings.
        assert {
            method
            for (path, method) in mounted_tables
            if path == "/api/tables/{table_id}"
        } == {"DELETE"}
        # Only the single-job status GET is mounted from the platform-jobs
        # router; the list and cancel sibling routes stay on the API.
        assert set(mounted_platform_jobs) == {("/api/platform-jobs/{job_id}", "GET")}

    def test_artifact_route_selection_is_exact(self):
        """Gate C4b mounts the artifact routes and the platform-job GET."""
        assert ARTIFACT_ROUTE_METHODS == {
            "/api/sdk/artifacts": frozenset({"GET", "POST"}),
            "/api/sdk/artifacts/document": frozenset({"POST"}),
            "/api/sdk/artifacts/spreadsheet": frozenset({"POST"}),
            "/api/sdk/artifacts/text": frozenset({"POST"}),
            "/api/sdk/artifacts/image": frozenset({"POST"}),
            "/api/sdk/artifacts/video": frozenset({"POST"}),
            "/api/sdk/artifacts/{artifact_id}/content": frozenset({"GET"}),
            "/api/sdk/artifacts/{artifact_id}/download-url": frozenset({"GET"}),
        }
        assert ARTIFACT_ROUTE_PATHS == frozenset(ARTIFACT_ROUTE_METHODS)
        assert PLATFORM_JOB_ROUTE_METHODS == {
            "/api/platform-jobs/{job_id}": frozenset({"GET"}),
        }
        assert ARTIFACT_ROUTE_PATHS.isdisjoint(
            CONFIG_ROUTE_PATHS
            | INTEGRATION_ROUTE_PATHS
            | TABLE_SDK_ROUTE_PATHS
            | FILES_ROUTE_PATHS
        )

    def test_files_route_selection_is_exact(self):
        """Gate C4a mounts the eight files facade routes as real objects."""
        assert FILES_ROUTE_PATHS == frozenset(
            {
                "/api/files/read",
                "/api/files/write",
                "/api/files/list",
                "/api/files/delete",
                "/api/files/stat",
                "/api/files/exists",
                "/api/files/signed-url",
                "/api/files/search",
            }
        )
        assert FILES_ROUTE_PATHS.isdisjoint(
            CONFIG_ROUTE_PATHS
            | INTEGRATION_ROUTE_PATHS
            | TABLE_SDK_ROUTE_PATHS
            | ARTIFACT_ROUTE_PATHS
        )

    def test_integration_route_selection_is_exact(self):
        """The six integrations routes are mounted as their real objects."""
        assert (
            SDK_ROUTE_PATHS
            == CONFIG_ROUTE_PATHS
            | INTEGRATION_ROUTE_PATHS
            | TABLE_SDK_ROUTE_PATHS
            | ARTIFACT_ROUTE_PATHS
        )
        assert len(INTEGRATION_ROUTE_PATHS) == 6
        assert all(
            path.startswith("/api/sdk/integrations/")
            for path in INTEGRATION_ROUTE_PATHS
        )

    def test_table_route_selection_is_exact(self):
        """Gate C3a/C3b mounts the two facade paths and every REST mutation."""
        assert TABLE_SDK_ROUTE_PATHS == frozenset(
            {"/api/sdk/tables/create", "/api/sdk/tables/list"}
        )
        assert TABLE_ROUTE_METHODS == {
            "/api/tables": frozenset({"POST"}),
            "/api/tables/{table_id}": frozenset({"DELETE"}),
            "/api/tables/{table_id}/documents": frozenset({"POST"}),
            "/api/tables/{table_id}/documents/upsert": frozenset({"POST"}),
            "/api/tables/{table_id}/documents/count": frozenset({"GET"}),
            "/api/tables/{table_id}/documents/{doc_id}": frozenset(
                {"GET", "PATCH", "DELETE"}
            ),
            "/api/tables/{table_id}/documents/query": frozenset({"POST"}),
            "/api/tables/{table_id}/documents/batch": frozenset({"POST"}),
            "/api/tables/{table_id}/documents/batch-delete": frozenset({"POST"}),
        }

    @pytest.mark.asyncio
    async def test_unknown_route_is_404(self):
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                response = await client.post("/api/sdk/health")
            assert response.status_code == 404
        finally:
            await server.stop()


class TestSocketServerLifecycle:
    @pytest.mark.asyncio
    async def test_start_binds_private_socket_and_stop_removes_it(self):
        server = WorkerSdkHttpServer()
        await server.start()
        path = server.socket_path
        assert path is not None
        assert os.path.exists(path)
        # Socket is owner-only; directory is owner-only.
        assert os.stat(path).st_mode & 0o777 == 0o600
        assert os.stat(os.path.dirname(path)).st_mode & 0o777 == 0o700

        await server.stop()
        assert server.socket_path is None
        assert not os.path.exists(path)
        assert not os.path.exists(os.path.dirname(path))

        # Idempotent.
        await server.stop()


class TestSocketServesConfigRoute:
    @pytest.mark.asyncio
    async def test_config_get_matches_the_real_route(self, committed_config, async_session_factory):
        from src.models.contracts.cli import CLIConfigGetRequest
        from src.routers.cli import cli_get_config

        key = f"wsdk-{uuid4().hex[:8]}"
        await committed_config(key, "socket-value")

        server = WorkerSdkHttpServer()
        await server.start()
        try:
            with _no_cache():
                async with _socket_client(server) as client:
                    response = await client.post(
                        "/api/sdk/config/get",
                        json={"key": key, "scope": "global"},
                        headers={"Authorization": f"Bearer {_engine_token()}"},
                    )
                assert response.status_code == 200, response.text
                payload = response.json()
                assert payload["value"] == "socket-value"
                assert payload["config_type"] == "string"

                async with async_session_factory() as session:
                    direct = await cli_get_config(
                        CLIConfigGetRequest(key=key, scope="global"),
                        _EngineUser(),
                        session,
                    )
                assert direct is not None
                assert payload == direct.model_dump()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_missing_key_is_null_and_not_an_error(self, committed_config):
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            with _no_cache():
                async with _socket_client(server) as client:
                    response = await client.post(
                        "/api/sdk/config/get",
                        json={
                            "key": f"absent-{uuid4().hex[:8]}",
                            "scope": "global",
                        },
                        headers={"Authorization": f"Bearer {_engine_token()}"},
                    )
            assert response.status_code == 200, response.text
            assert response.json() is None
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_scope_rules_match_the_api(self):
        """A non-superuser caller may read its own org but not another."""
        from src.core.security import create_access_token

        org_a = uuid4()
        org_b = uuid4()
        token = create_access_token(
            {
                "sub": str(uuid4()),
                "email": "user@example.com",
                "name": "User",
                "org_id": str(org_a),
            }
        )
        headers = {"Authorization": f"Bearer {token}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            with _no_cache():
                async with _socket_client(server) as client:
                    own = await client.post(
                        "/api/sdk/config/get",
                        json={"key": "k", "scope": None},
                        headers=headers,
                    )
                    denied = await client.post(
                        "/api/sdk/config/get",
                        json={"key": "k", "scope": str(org_b)},
                        headers=headers,
                    )
            assert own.status_code == 200, own.text
            assert denied.status_code == 403, denied.text
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_auth_is_required(self):
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                missing = await client.post(
                    "/api/sdk/config/get",
                    json={"key": "k", "scope": "global"},
                )
                invalid = await client.post(
                    "/api/sdk/config/get",
                    json={"key": "k", "scope": "global"},
                    headers={"Authorization": "Bearer not-a-token"},
                )
            assert missing.status_code == 401
            assert invalid.status_code == 401
        finally:
            await server.stop()


class TestSocketConfigContracts:
    """The socket carries the same config contracts as the network API.

    ``TestSocketServesConfigRoute`` covers ``config.get``; these cover the
    mutation operations and the secret/missing/scope/validation contracts the
    facade now sends over the shared client transport for all four methods.
    """

    @pytest.mark.asyncio
    async def test_secret_set_get_list_over_socket(self):
        key = f"wsdk-secret-{uuid4().hex[:8]}"
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            with _no_cache():
                async with _socket_client(server) as client:
                    set_response = await client.post(
                        "/api/sdk/config/set",
                        json={
                            "key": key,
                            "value": "top-secret",
                            "is_secret": True,
                            "scope": "global",
                        },
                        headers=headers,
                    )
                    get_response = await client.post(
                        "/api/sdk/config/get",
                        json={"key": key, "scope": "global"},
                        headers=headers,
                    )
                    list_response = await client.post(
                        "/api/sdk/config/list",
                        json={"scope": "global"},
                        headers=headers,
                    )
                    delete_response = await client.post(
                        "/api/sdk/config/delete",
                        json={"key": key, "scope": "global"},
                        headers=headers,
                    )
            assert set_response.status_code == 204, set_response.text
            assert get_response.status_code == 200, get_response.text
            payload = get_response.json()
            assert payload["value"] == "top-secret"
            assert payload["config_type"] == "secret"
            assert list_response.status_code == 200, list_response.text
            assert list_response.json()[key] == "[SECRET]"
            assert delete_response.status_code == 200, delete_response.text
            assert delete_response.json() is True
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_scope_rules_match_for_mutations(self, async_session_factory):
        """A non-superuser may mutate its own org but not another."""
        from sqlalchemy import delete

        from src.core.security import create_access_token
        from src.models.orm.config import Config as ConfigModel
        from src.models.orm.organizations import Organization as OrganizationModel

        async with async_session_factory() as session:
            org_a_row = OrganizationModel(
                name=f"wsdk-scope-a-{uuid4().hex[:8]}",
                is_active=True,
                is_provider=False,
                created_by="worker-sdk-http-test",
            )
            org_b_row = OrganizationModel(
                name=f"wsdk-scope-b-{uuid4().hex[:8]}",
                is_active=True,
                is_provider=False,
                created_by="worker-sdk-http-test",
            )
            session.add_all([org_a_row, org_b_row])
            await session.commit()
            org_a, org_b = org_a_row.id, org_b_row.id

        token = create_access_token(
            {
                "sub": str(uuid4()),
                "email": "user@example.com",
                "name": "User",
                "org_id": str(org_a),
            }
        )
        headers = {"Authorization": f"Bearer {token}"}
        key = f"wsdk-scope-{uuid4().hex[:8]}"
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            with _no_cache():
                async with _socket_client(server) as client:
                    own_set = await client.post(
                        "/api/sdk/config/set",
                        json={
                            "key": key,
                            "value": "own",
                            "is_secret": False,
                            "scope": str(org_a),
                        },
                        headers=headers,
                    )
                    denied_set = await client.post(
                        "/api/sdk/config/set",
                        json={
                            "key": key,
                            "value": "other",
                            "is_secret": False,
                            "scope": str(org_b),
                        },
                        headers=headers,
                    )
                    denied_list = await client.post(
                        "/api/sdk/config/list",
                        json={"scope": str(org_b)},
                        headers=headers,
                    )
                    denied_delete = await client.post(
                        "/api/sdk/config/delete",
                        json={"key": key, "scope": str(org_b)},
                        headers=headers,
                    )
                    cleanup = await client.post(
                        "/api/sdk/config/delete",
                        json={"key": key, "scope": str(org_a)},
                        headers=headers,
                    )
            assert own_set.status_code == 204, own_set.text
            assert denied_set.status_code == 403, denied_set.text
            assert denied_list.status_code == 403, denied_list.text
            assert denied_delete.status_code == 403, denied_delete.text
            assert cleanup.status_code == 200, cleanup.text
            assert cleanup.json() is True
        finally:
            await server.stop()
            async with async_session_factory() as session:
                await session.execute(
                    delete(ConfigModel).where(ConfigModel.key == key)
                )
                await session.execute(
                    delete(OrganizationModel).where(
                        OrganizationModel.id.in_([org_a, org_b])
                    )
                )
                await session.commit()

    @pytest.mark.asyncio
    async def test_malformed_scope_is_422_over_socket(self):
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            with _no_cache():
                async with _socket_client(server) as client:
                    set_response = await client.post(
                        "/api/sdk/config/set",
                        json={
                            "key": "k",
                            "value": 1,
                            "is_secret": False,
                            "scope": "not-a-uuid",
                        },
                        headers=headers,
                    )
                    list_response = await client.post(
                        "/api/sdk/config/list",
                        json={"scope": "not-a-uuid"},
                        headers=headers,
                    )
                    delete_response = await client.post(
                        "/api/sdk/config/delete",
                        json={"key": "k", "scope": "not-a-uuid"},
                        headers=headers,
                    )
            assert set_response.status_code == 422, set_response.text
            assert list_response.status_code == 422, list_response.text
            assert delete_response.status_code == 422, delete_response.text
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_delete_missing_key_returns_false_over_socket(self):
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            with _no_cache():
                async with _socket_client(server) as client:
                    response = await client.post(
                        "/api/sdk/config/delete",
                        json={
                            "key": f"absent-{uuid4().hex[:8]}",
                            "scope": "global",
                        },
                        headers=headers,
                    )
            assert response.status_code == 200, response.text
            assert response.json() is False
        finally:
            await server.stop()


class TestSocketTableWrites:
    """Gate C3b: the socket carries the table write routes and commits them."""

    @pytest.mark.asyncio
    async def test_batch_write_over_socket_commits(self, async_session_factory):
        from sqlalchemy import delete, select

        from shared.policies.probe import make_seed_admin_bypass
        from src.models.orm.organizations import Organization as OrganizationModel
        from src.models.orm.tables import Document as DocumentModel
        from src.models.orm.tables import Table as TableModel

        table_uuid = None
        org_uuid = None
        async with async_session_factory() as session:
            org = OrganizationModel(
                name=f"wsdk-write-{uuid4().hex[:8]}",
                is_active=True,
                is_provider=False,
                created_by="worker-sdk-http-test",
            )
            session.add(org)
            await session.flush()
            table = TableModel(
                name=f"wsdk_tbl_{uuid4().hex[:8]}",
                organization_id=org.id,
                schema={"columns": []},
                access=make_seed_admin_bypass(),
                created_by="worker-sdk-http-test",
            )
            session.add(table)
            await session.commit()
            org_uuid, table_uuid = org.id, table.id

        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                response = await client.post(
                    f"/api/tables/{table_uuid}/documents/batch",
                    params={"scope": str(org_uuid)},
                    json={
                        "documents": [{"id": "b1", "data": {"v": 1}}],
                        "upsert": False,
                    },
                    headers=headers,
                )
            assert response.status_code == 200, response.text
            assert response.json()["inserted"] == 1

            # An independent session must see the committed row. A success
            # response that only the serving session can observe is not a
            # durable write.
            async with async_session_factory() as check:
                row = (
                    await check.execute(
                        select(DocumentModel).where(
                            DocumentModel.table_id == table_uuid,
                            DocumentModel.id == "b1",
                        )
                    )
                ).scalar_one_or_none()
            assert row is not None, "socket batch write returned 200 without committing"
        finally:
            await server.stop()
            async with async_session_factory() as cleanup:
                await cleanup.execute(
                    delete(DocumentModel).where(DocumentModel.table_id == table_uuid)
                )
                await cleanup.execute(
                    delete(TableModel).where(TableModel.id == table_uuid)
                )
                await cleanup.execute(
                    delete(OrganizationModel).where(OrganizationModel.id == org_uuid)
                )
                await cleanup.commit()


class TestEngineLocalTransportSelection:
    """The shared BifrostClient owns one engine-local transport choice.

    ``engine_request`` / ``engine_request_sync`` are the only engine-local
    entry points; ordinary verbs never carry a transport flag. The transport
    is the worker Unix socket when the engine injected one, otherwise the
    ordinary network client, and a local attempt never replays over the
    network.
    """

    @pytest.mark.asyncio
    async def test_engine_local_transport_requires_injection(self):
        """No trusted injection means no engine-local transport to build."""
        client = BifrostClient("http://dead-api", "token")
        assert get_engine_socket_path() is None
        with pytest.raises(RuntimeError, match="not installed"):
            client._get_engine_async_client()
        with pytest.raises(RuntimeError, match="not installed"):
            client._get_engine_sync_client()

    def test_engine_local_entry_points_select_transport(self):
        """The engine-local entry points use the socket once injected."""
        client = BifrostClient("http://dead-api", "token")
        network_async = client._get_async_client()
        # Without injection an engine-local request outside an engine has no
        # local transport to prefer, so it keeps the normal network client.
        assert client._async_http_for(engine_local=False) is network_async
        assert client._async_http_for(engine_local=True) is network_async
        assert client._sync_http_for(engine_local=True) is client._sync_http

        _install_engine_socket("/tmp/bifrost-not-running.sock")
        # An engine-local request now resolves to the socket; an ordinary one
        # never does.
        assert client._async_http_for(engine_local=True) is client._get_engine_async_client()
        assert client._async_http_for(engine_local=False) is network_async
        assert client._sync_http_for(engine_local=True) is client._get_engine_sync_client()

    def test_engine_clients_are_cached_and_rebind_on_path(self):
        """Same socket path reuses one client; a new injection rebuilds it."""
        client = BifrostClient("http://dead-api", "token")
        _install_engine_socket(f"/tmp/bifrost-a-{uuid4().hex}.sock")
        async_client = client._get_engine_async_client()
        sync_client = client._get_engine_sync_client()
        assert client._get_engine_async_client() is async_client
        assert client._get_engine_sync_client() is sync_client

        _install_engine_socket(f"/tmp/bifrost-b-{uuid4().hex}.sock")
        assert client._get_engine_async_client() is not async_client
        assert client._get_engine_sync_client() is not sync_client

    def test_engine_async_client_rebinds_per_event_loop(self):
        """A cached async client is not reused across event loops."""
        client = BifrostClient("http://dead-api", "token")
        _install_engine_socket(f"/tmp/bifrost-loop-{uuid4().hex}.sock")

        async def _build_and_close() -> httpx.AsyncClient:
            http = client._get_engine_async_client()
            await http.aclose()
            return http

        first = asyncio.run(_build_and_close())
        second = asyncio.run(_build_and_close())
        assert first is not second

    @pytest.mark.asyncio
    async def test_config_get_uses_socket_when_injected(self, committed_config):
        from bifrost import config as bifrost_config

        key = f"wsdk-hook-{uuid4().hex[:8]}"
        await committed_config(key, "via-socket")

        server = WorkerSdkHttpServer()
        await server.start()
        try:
            _install_engine_socket(server.socket_path)  # type: ignore[arg-type]
            client = BifrostClient("http://dead-api", _engine_token())
            _set_client(client)
            with _no_cache():
                value = await bifrost_config.get(key, scope="global")
            assert value == "via-socket"
            # The proof rides the client's cached engine-local HTTPX client,
            # not a per-call AsyncClient.
            assert client._engine_http is not None
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_engine_request_sync_round_trip_over_socket(self, committed_config):
        """The sync engine-local entry point reaches the same real route."""
        key = f"wsdk-sync-{uuid4().hex[:8]}"
        await committed_config(key, "sync-value")

        server = WorkerSdkHttpServer()
        await server.start()
        try:
            _install_engine_socket(server.socket_path)  # type: ignore[arg-type]
            client = BifrostClient("http://dead-api", _engine_token())
            with _no_cache():
                response = await asyncio.to_thread(
                    client.engine_request_sync,
                    "POST",
                    "/api/sdk/config/get",
                    json={"key": key, "scope": "global"},
                )
            assert response.status_code == 200, response.text
            assert response.json()["value"] == "sync-value"
            # Reused pooled sync client, not a per-call client.
            assert client._engine_sync_http is not None
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_engine_streaming_transport_over_socket(self, committed_config):
        """The engine-local async client streams the same real route."""
        import json

        key = f"wsdk-stream-{uuid4().hex[:8]}"
        await committed_config(key, "stream-value")

        server = WorkerSdkHttpServer()
        await server.start()
        try:
            _install_engine_socket(server.socket_path)  # type: ignore[arg-type]
            client = BifrostClient("http://dead-api", _engine_token())
            engine = client._get_engine_async_client()
            with _no_cache():
                async with engine.stream(
                    "POST",
                    "/api/sdk/config/get",
                    json={"key": key, "scope": "global"},
                ) as response:
                    assert response.status_code == 200
                    payload = json.loads(await response.aread())
            assert payload["value"] == "stream-value"
            assert client._get_engine_async_client() is engine
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_engine_local_401_does_not_refresh_or_replay(self):
        """A local 401 surfaces; it never triggers network refresh/replay."""
        from bifrost import config as bifrost_config

        server = WorkerSdkHttpServer()
        await server.start()
        try:
            _install_engine_socket(server.socket_path)  # type: ignore[arg-type]
            client = BifrostClient("http://dead-api", "not-a-token")
            _set_client(client)

            refresh_attempts: list[str] = []
            original_refresh = client._refresh_and_update

            async def _spy(observed: str | None = None) -> bool:
                refresh_attempts.append("called")
                return await original_refresh(observed)

            client._refresh_and_update = _spy  # type: ignore[method-assign]
            with pytest.raises(httpx.HTTPStatusError):
                await bifrost_config.get("k", scope="global")
            assert refresh_attempts == []
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_external_config_get_uses_shared_client(self):
        from bifrost import config as bifrost_config

        assert get_engine_socket_path() is None
        response = httpx.Response(
            200,
            json={"key": "k", "value": "via-network", "config_type": "string"},
            request=httpx.Request("POST", "http://api/api/sdk/config/get"),
        )
        client = MagicMock()
        client.engine_request = AsyncMock(return_value=response)
        with patch("bifrost.config.get_client", return_value=client):
            value = await bifrost_config.get("k", scope="global")

        assert value == "via-network"
        client.engine_request.assert_awaited_once_with(
            "POST",
            "/api/sdk/config/get",
            json={"key": "k", "scope": "global"},
        )

    @pytest.mark.asyncio
    async def test_external_config_mutations_use_network_path(self):
        """Outside an engine the same shared-client entry point is the network."""
        from bifrost import config as bifrost_config

        assert get_engine_socket_path() is None
        set_response = httpx.Response(
            204,
            request=httpx.Request("POST", "http://api/api/sdk/config/set"),
        )
        list_response = httpx.Response(
            200,
            json={"a": 1},
            request=httpx.Request("POST", "http://api/api/sdk/config/list"),
        )
        delete_response = httpx.Response(
            200,
            json=True,
            request=httpx.Request("POST", "http://api/api/sdk/config/delete"),
        )
        client = MagicMock()
        client.engine_request = AsyncMock(
            side_effect=[set_response, list_response, delete_response]
        )
        with patch("bifrost.config.get_client", return_value=client):
            await bifrost_config.set("k", "v", scope="global")
            listed = await bifrost_config.list(scope="global")
            deleted = await bifrost_config.delete("k", scope="global")

        assert listed["a"] == 1
        assert deleted is True
        assert [
            (call.args[0], call.args[1])
            for call in client.engine_request.await_args_list
        ] == [
            ("POST", "/api/sdk/config/set"),
            ("POST", "/api/sdk/config/list"),
            ("POST", "/api/sdk/config/delete"),
        ]

    @pytest.mark.asyncio
    async def test_config_set_get_list_delete_over_socket(self):
        """All four config methods ride the injected socket, no network API."""
        from bifrost import config as bifrost_config

        key = f"wsdk-crud-{uuid4().hex[:8]}"
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            _install_engine_socket(server.socket_path)  # type: ignore[arg-type]
            client = BifrostClient("http://dead-api", _engine_token())
            _set_client(client)
            with _no_cache():
                await bifrost_config.set(key, "socket-crud", scope="global")
                assert (
                    await bifrost_config.get(key, scope="global")
                    == "socket-crud"
                )
                listed = await bifrost_config.list(scope="global")
                assert listed[key] == "socket-crud"
                assert (
                    await bifrost_config.delete(key, scope="global") is True
                )
                assert (
                    await bifrost_config.get(
                        key, default="gone", scope="global"
                    )
                    == "gone"
                )
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_large_config_value_over_socket(self):
        """A large value round-trips over HTTP without a channel wrapper."""
        from bifrost import config as bifrost_config

        key = f"wsdk-large-{uuid4().hex[:8]}"
        big = {"blob": "y" * 100_000}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            _install_engine_socket(server.socket_path)  # type: ignore[arg-type]
            client = BifrostClient("http://dead-api", _engine_token())
            _set_client(client)
            with _no_cache():
                await bifrost_config.set(key, big, scope="global")
                assert await bifrost_config.get(key, scope="global") == big
                assert await bifrost_config.delete(key, scope="global") is True
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_local_failure_does_not_fall_back_to_network(self):
        from bifrost import config as bifrost_config

        # An injected path with nothing listening: the local attempt must
        # raise rather than silently retry over the network API.
        missing_socket = os.path.join(
            "/tmp", f"bifrost-missing-{uuid4().hex}.sock"
        )
        _install_engine_socket(missing_socket)
        _set_client(BifrostClient("http://dead-api", "token"))

        with pytest.raises(httpx.ConnectError):
            await bifrost_config.get("k", scope="global")
        with pytest.raises(httpx.ConnectError):
            await bifrost_config.set("k", "v", scope="global")
        with pytest.raises(httpx.ConnectError):
            await bifrost_config.list(scope="global")
        with pytest.raises(httpx.ConnectError):
            await bifrost_config.delete("k", scope="global")


class TestSocketFiles:
    """Gate C4a: the socket serves the real files routes with their DTOs."""

    @pytest.mark.asyncio
    async def test_malformed_file_body_is_422_over_socket(self):
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                # Missing required path and an invalid mode must both be
                # rejected by the routes' real DTOs, not a copied validator.
                stat = await client.post(
                    "/api/files/stat", json={}, headers=headers
                )
                read = await client.post(
                    "/api/files/read",
                    json={"path": "x.txt", "mode": "ftp"},
                    headers=headers,
                )
            assert stat.status_code == 422, stat.text
            assert read.status_code == 422, read.text
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_missing_workspace_read_is_404_over_socket(self):
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                response = await client.post(
                    "/api/files/read",
                    json={
                        "path": f"wsdk-missing-{uuid4().hex}/nope.txt",
                        "location": "workspace",
                        "mode": "cloud",
                        "binary": False,
                        "scope": None,
                    },
                    headers=headers,
                )
            assert response.status_code == 404, response.text
        finally:
            await server.stop()


class TestEngineLocalFilesFallback:
    """A failed files socket request never replays over the network API."""

    @pytest.mark.asyncio
    async def test_files_local_failure_does_not_fall_back_to_network(self):
        from bifrost.files import files

        missing_socket = os.path.join(
            "/tmp", f"bifrost-missing-{uuid4().hex}.sock"
        )
        _install_engine_socket(missing_socket)
        _set_client(BifrostClient("http://dead-api", "token"))

        with pytest.raises(httpx.ConnectError):
            await files.read("a.txt")
        with pytest.raises(httpx.ConnectError):
            await files.write("a.txt", "x")
        with pytest.raises(httpx.ConnectError):
            await files.list("")
        with pytest.raises(httpx.ConnectError):
            await files.read_bytes("a.bin")


class TestSocketArtifacts:
    """Gate C4b: the socket serves the real artifact and platform-job routes."""

    @pytest.mark.asyncio
    async def test_malformed_artifact_body_is_422_over_socket(self):
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                text = await client.post(
                    "/api/sdk/artifacts/text", json={}, headers=headers
                )
                document = await client.post(
                    "/api/sdk/artifacts/document", json={}, headers=headers
                )
            assert text.status_code == 422, text.text
            assert document.status_code == 422, document.text
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_list_without_workspace_is_422_over_socket(self):
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                response = await client.get("/api/sdk/artifacts", headers=headers)
            assert response.status_code == 422, response.text
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_missing_platform_job_is_404_over_socket(self):
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                response = await client.get(
                    f"/api/platform-jobs/{uuid4()}", headers=headers
                )
            assert response.status_code == 404, response.text
        finally:
            await server.stop()


class TestEngineLocalArtifactsFallback:
    """A failed artifact socket request never replays over the network API."""

    @pytest.mark.asyncio
    async def test_artifact_local_failure_does_not_fall_back_to_network(self):
        from bifrost.artifacts import artifacts

        missing_socket = os.path.join(
            "/tmp", f"bifrost-missing-{uuid4().hex}.sock"
        )
        _install_engine_socket(missing_socket)
        _set_client(BifrostClient("http://dead-api", "token"))

        ref = {
            "type": "bifrost_artifact",
            "id": str(uuid4()),
            "filename": "note.md",
            "content_type": "text/markdown",
            "size_bytes": 1,
        }
        with pytest.raises(httpx.ConnectError):
            await artifacts.write("note.md", b"x", content_type="text/markdown")
        with pytest.raises(httpx.ConnectError):
            await artifacts.read(ref)
        with pytest.raises(httpx.ConnectError):
            await artifacts.get_download_url(ref)
        with pytest.raises(httpx.ConnectError):
            await artifacts.create_text(
                "note", format="markdown", content="# note"
            )
        # ``create_video`` fails at enqueue with the transport error — never a
        # made-up channel deadline.
        with pytest.raises(httpx.ConnectError):
            await artifacts.create_video("launch", prompt="A launch video")
