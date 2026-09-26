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
    CONFIG_ROUTE_PATHS,
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

    def test_build_app_mounts_real_config_routes_by_identity(self):
        from fastapi.routing import APIRoute

        from src.routers.cli import router as sdk_router

        originals = {
            route.path: route
            for route in sdk_router.routes
            if getattr(route, "path", None) in CONFIG_ROUTE_PATHS
        }
        assert set(originals) == CONFIG_ROUTE_PATHS

        app = build_worker_sdk_app()
        mounted = {
            route.path: route
            for route in app.router.routes
            if isinstance(route, APIRoute)
        }
        # Only the selected routes, and the exact registered objects — no
        # copied handlers and no rest of the API surface.
        assert set(mounted) == CONFIG_ROUTE_PATHS
        for path, route in mounted.items():
            assert route is originals[path]
            assert route.endpoint is originals[path].endpoint

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
