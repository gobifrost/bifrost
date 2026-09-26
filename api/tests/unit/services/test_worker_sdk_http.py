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
    AGENT_RUN_ROUTE_METHODS,
    AI_ROUTE_METHODS,
    AI_ROUTE_PATHS,
    ARTIFACT_ROUTE_METHODS,
    ARTIFACT_ROUTE_PATHS,
    CONFIG_ROUTE_PATHS,
    EVENT_ROUTE_METHODS,
    EXECUTION_ROUTE_METHODS,
    FILES_ROUTE_PATHS,
    FORM_ROUTE_METHODS,
    INTEGRATION_ROUTE_PATHS,
    KNOWLEDGE_ROUTE_PATHS,
    ORGANIZATION_ROUTE_METHODS,
    PLATFORM_JOB_ROUTE_METHODS,
    ROLES_ROUTE_METHODS,
    SDK_ROUTE_PATHS,
    TABLE_ROUTE_METHODS,
    TABLE_SDK_ROUTE_PATHS,
    USER_ROUTE_METHODS,
    WORKFLOW_ROUTE_METHODS,
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

        from src.routers.agent_runs import router as agent_runs_router
        from src.routers.cli import router as sdk_router
        from src.routers.events import router as events_router
        from src.routers.executions import router as executions_router
        from src.routers.files import router as files_router
        from src.routers.forms import router as forms_router
        from src.routers.organizations import router as organizations_router
        from src.routers.platform_jobs import router as platform_jobs_router
        from src.routers.roles import router as roles_router
        from src.routers.tables import router as tables_router
        from src.routers.users import router as users_router
        from src.routers.workflows import router as workflows_router

        cli_path_only = (
            CONFIG_ROUTE_PATHS
            | INTEGRATION_ROUTE_PATHS
            | TABLE_SDK_ROUTE_PATHS
            | KNOWLEDGE_ROUTE_PATHS
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

        ai_originals = {
            (route.path, method): route
            for route in sdk_router.routes
            if getattr(route, "path", None) in AI_ROUTE_METHODS
            for method in (getattr(route, "methods", None) or set())
            & AI_ROUTE_METHODS[route.path]
        }
        assert set(ai_originals) == {
            (path, method)
            for path, methods in AI_ROUTE_METHODS.items()
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

        workflows_originals = {
            (route.path, method): route
            for route in workflows_router.routes
            if (wanted := WORKFLOW_ROUTE_METHODS.get(getattr(route, "path", None)))
            for method in (getattr(route, "methods", None) or set()) & wanted
        }
        executions_originals = {
            (route.path, method): route
            for route in executions_router.routes
            if (wanted := EXECUTION_ROUTE_METHODS.get(getattr(route, "path", None)))
            for method in (getattr(route, "methods", None) or set()) & wanted
        }
        agent_run_originals = {
            (route.path, method): route
            for route in agent_runs_router.routes
            if (wanted := AGENT_RUN_ROUTE_METHODS.get(getattr(route, "path", None)))
            for method in (getattr(route, "methods", None) or set()) & wanted
        }
        event_originals = {
            (route.path, method): route
            for route in events_router.routes
            if (wanted := EVENT_ROUTE_METHODS.get(getattr(route, "path", None)))
            for method in (getattr(route, "methods", None) or set()) & wanted
        }
        form_originals = {
            (route.path, method): route
            for route in forms_router.routes
            if (wanted := FORM_ROUTE_METHODS.get(getattr(route, "path", None)))
            for method in (getattr(route, "methods", None) or set()) & wanted
        }
        organization_originals = {
            (route.path, method): route
            for route in organizations_router.routes
            if (wanted := ORGANIZATION_ROUTE_METHODS.get(getattr(route, "path", None)))
            for method in (getattr(route, "methods", None) or set()) & wanted
        }
        user_originals = {
            (route.path, method): route
            for route in users_router.routes
            if (wanted := USER_ROUTE_METHODS.get(getattr(route, "path", None)))
            for method in (getattr(route, "methods", None) or set()) & wanted
        }
        roles_originals = {
            (route.path, method): route
            for route in roles_router.routes
            if (wanted := ROLES_ROUTE_METHODS.get(getattr(route, "path", None)))
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
        mounted_ai = {
            (route.path, method): route
            for route in mounted
            if route.path in AI_ROUTE_METHODS
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
        mounted_workflows = {
            (route.path, method): route
            for route in mounted
            if route.path in WORKFLOW_ROUTE_METHODS
            for method in route.methods
        }
        mounted_executions = {
            (route.path, method): route
            for route in mounted
            if route.path in EXECUTION_ROUTE_METHODS
            for method in route.methods
        }
        mounted_agent_runs = {
            (route.path, method): route
            for route in mounted
            if route.path in AGENT_RUN_ROUTE_METHODS
            for method in route.methods
        }
        mounted_events = {
            (route.path, method): route
            for route in mounted
            if route.path in EVENT_ROUTE_METHODS
            for method in route.methods
        }
        mounted_forms = {
            (route.path, method): route
            for route in mounted
            if route.path in FORM_ROUTE_METHODS
            for method in route.methods
        }
        mounted_organizations = {
            (route.path, method): route
            for route in mounted
            if route.path in ORGANIZATION_ROUTE_METHODS
            for method in route.methods
        }
        mounted_users = {
            (route.path, method): route
            for route in mounted
            if route.path in USER_ROUTE_METHODS
            for method in route.methods
        }
        mounted_roles = {
            (route.path, method): route
            for route in mounted
            if route.path in ROLES_ROUTE_METHODS
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

        assert set(mounted_ai) == set(ai_originals)
        for key, route in mounted_ai.items():
            assert route is ai_originals[key]
            assert route.endpoint is ai_originals[key].endpoint

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

        assert set(mounted_workflows) == set(workflows_originals)
        for key, route in mounted_workflows.items():
            assert route is workflows_originals[key]
            assert route.endpoint is workflows_originals[key].endpoint

        assert set(mounted_executions) == set(executions_originals)
        for key, route in mounted_executions.items():
            assert route is executions_originals[key]
            assert route.endpoint is executions_originals[key].endpoint

        assert set(mounted_agent_runs) == set(agent_run_originals)
        for key, route in mounted_agent_runs.items():
            assert route is agent_run_originals[key]
            assert route.endpoint is agent_run_originals[key].endpoint

        assert set(mounted_events) == set(event_originals)
        for key, route in mounted_events.items():
            assert route is event_originals[key]
            assert route.endpoint is event_originals[key].endpoint

        assert set(mounted_forms) == set(form_originals)
        for key, route in mounted_forms.items():
            assert route is form_originals[key]
            assert route.endpoint is form_originals[key].endpoint

        assert set(mounted_organizations) == set(organization_originals)
        for key, route in mounted_organizations.items():
            assert route is organization_originals[key]
            assert route.endpoint is organization_originals[key].endpoint

        assert set(mounted_users) == set(user_originals)
        for key, route in mounted_users.items():
            assert route is user_originals[key]
            assert route.endpoint is user_originals[key].endpoint

        assert set(mounted_roles) == set(roles_originals)
        for key, route in mounted_roles.items():
            assert route is roles_originals[key]
            assert route.endpoint is roles_originals[key].endpoint

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
            | KNOWLEDGE_ROUTE_PATHS
        )

    def test_ai_route_selection_is_exact(self):
        """Gate C5f mounts only the AI completion POST and model-info GET."""
        assert AI_ROUTE_METHODS == {
            "/api/sdk/ai/complete": frozenset({"POST"}),
            "/api/sdk/ai/info": frozenset({"GET"}),
        }
        assert AI_ROUTE_PATHS == frozenset(AI_ROUTE_METHODS)
        # The streaming route stays on its existing channel path.
        assert "/api/sdk/ai/stream" not in AI_ROUTE_PATHS
        assert AI_ROUTE_PATHS.isdisjoint(
            CONFIG_ROUTE_PATHS
            | INTEGRATION_ROUTE_PATHS
            | TABLE_SDK_ROUTE_PATHS
            | ARTIFACT_ROUTE_PATHS
            | FILES_ROUTE_PATHS
            | KNOWLEDGE_ROUTE_PATHS
        )

    def test_knowledge_route_selection_is_exact(self):
        """Gate C4c mounts the seven knowledge facade routes as real objects."""
        assert KNOWLEDGE_ROUTE_PATHS == frozenset(
            {
                "/api/sdk/knowledge/store",
                "/api/sdk/knowledge/store-many",
                "/api/sdk/knowledge/search",
                "/api/sdk/knowledge/delete",
                "/api/sdk/knowledge/namespace/{namespace}",
                "/api/sdk/knowledge/namespaces",
                "/api/sdk/knowledge/get",
            }
        )
        assert KNOWLEDGE_ROUTE_PATHS.isdisjoint(
            CONFIG_ROUTE_PATHS
            | INTEGRATION_ROUTE_PATHS
            | TABLE_SDK_ROUTE_PATHS
            | ARTIFACT_ROUTE_PATHS
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
            | KNOWLEDGE_ROUTE_PATHS
            | AI_ROUTE_PATHS
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

    def test_workflow_and_execution_route_selection_is_exact(self):
        """Gate C5a mounts the workflow/execution facade routes as real objects."""
        assert WORKFLOW_ROUTE_METHODS == {
            "/api/workflows": frozenset({"GET"}),
            "/api/workflows/execute": frozenset({"POST"}),
            "/api/workflows/executions/{execution_id}/cancel": frozenset({"POST"}),
        }
        assert EXECUTION_ROUTE_METHODS == {
            "/api/executions": frozenset({"GET"}),
            "/api/executions/{execution_id}": frozenset({"GET"}),
        }
        assert set(WORKFLOW_ROUTE_METHODS).isdisjoint(EXECUTION_ROUTE_METHODS)
        assert set(EXECUTION_ROUTE_METHODS).isdisjoint(SDK_ROUTE_PATHS)
        assert set(WORKFLOW_ROUTE_METHODS).isdisjoint(SDK_ROUTE_PATHS)

    def test_agent_run_route_selection_is_exact(self):
        """Gate C5b mounts only the enqueue POST and the detail GET."""
        assert AGENT_RUN_ROUTE_METHODS == {
            "/api/agent-runs/enqueue": frozenset({"POST"}),
            "/api/agent-runs/{run_id}": frozenset({"GET"}),
        }
        assert set(AGENT_RUN_ROUTE_METHODS).isdisjoint(
            SDK_ROUTE_PATHS
            | set(WORKFLOW_ROUTE_METHODS)
            | set(EXECUTION_ROUTE_METHODS)
        )

    def test_event_and_form_route_selection_is_exact(self):
        """Gate C5c mounts only the emit POST and the two form GET reads."""
        assert EVENT_ROUTE_METHODS == {
            "/api/events/emit": frozenset({"POST"}),
        }
        assert FORM_ROUTE_METHODS == {
            "/api/forms": frozenset({"GET"}),
            "/api/forms/{form_id}": frozenset({"GET"}),
        }
        assert set(EVENT_ROUTE_METHODS).isdisjoint(
            SDK_ROUTE_PATHS
            | set(WORKFLOW_ROUTE_METHODS)
            | set(EXECUTION_ROUTE_METHODS)
            | set(AGENT_RUN_ROUTE_METHODS)
        )
        assert set(FORM_ROUTE_METHODS).isdisjoint(
            SDK_ROUTE_PATHS
            | set(WORKFLOW_ROUTE_METHODS)
            | set(EXECUTION_ROUTE_METHODS)
            | set(AGENT_RUN_ROUTE_METHODS)
            | set(EVENT_ROUTE_METHODS)
        )

    def test_organization_and_user_route_selection_is_exact(self):
        """Gate C5d mounts only the five org and five user facade routes."""
        assert ORGANIZATION_ROUTE_METHODS == {
            "/api/organizations": frozenset({"GET", "POST"}),
            "/api/organizations/{org_id}": frozenset({"GET", "PATCH", "DELETE"}),
        }
        assert USER_ROUTE_METHODS == {
            "/api/users": frozenset({"GET", "POST"}),
            "/api/users/{user_id}": frozenset({"GET", "PATCH", "DELETE"}),
        }
        assert set(ORGANIZATION_ROUTE_METHODS).isdisjoint(
            SDK_ROUTE_PATHS
            | set(WORKFLOW_ROUTE_METHODS)
            | set(EXECUTION_ROUTE_METHODS)
            | set(AGENT_RUN_ROUTE_METHODS)
            | set(EVENT_ROUTE_METHODS)
            | set(FORM_ROUTE_METHODS)
        )
        assert set(USER_ROUTE_METHODS).isdisjoint(
            SDK_ROUTE_PATHS
            | set(WORKFLOW_ROUTE_METHODS)
            | set(EXECUTION_ROUTE_METHODS)
            | set(AGENT_RUN_ROUTE_METHODS)
            | set(EVENT_ROUTE_METHODS)
            | set(FORM_ROUTE_METHODS)
            | set(ORGANIZATION_ROUTE_METHODS)
        )

    def test_role_route_selection_is_exact(self):
        """Gate C5e mounts only the nine role facade (path, method) pairs."""
        assert ROLES_ROUTE_METHODS == {
            "/api/roles": frozenset({"GET", "POST"}),
            "/api/roles/{role_id}": frozenset({"GET", "PATCH", "DELETE"}),
            "/api/roles/{role_id}/users": frozenset({"GET", "POST"}),
            "/api/roles/{role_id}/forms": frozenset({"GET", "POST"}),
        }
        assert set(ROLES_ROUTE_METHODS).isdisjoint(
            SDK_ROUTE_PATHS
            | set(WORKFLOW_ROUTE_METHODS)
            | set(EXECUTION_ROUTE_METHODS)
            | set(AGENT_RUN_ROUTE_METHODS)
            | set(EVENT_ROUTE_METHODS)
            | set(FORM_ROUTE_METHODS)
            | set(ORGANIZATION_ROUTE_METHODS)
            | set(USER_ROUTE_METHODS)
        )

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


class TestSocketKnowledge:
    """Gate C4c: the socket serves the real knowledge routes with their DTOs."""

    @pytest.mark.asyncio
    async def test_external_caller_denied_over_socket(self):
        from src.core.security import create_access_token

        token = create_access_token(
            {
                "sub": str(uuid4()),
                "email": "external@example.com",
                "name": "External",
                "org_id": str(uuid4()),
                "is_external": True,
            }
        )
        headers = {"Authorization": f"Bearer {token}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                response = await client.post(
                    "/api/sdk/knowledge/store",
                    json={"content": "x", "namespace": "ns"},
                    headers=headers,
                )
            assert response.status_code == 403, response.text
            assert response.json()["detail"] == (
                "External users cannot access the knowledge store directly"
            )
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_malformed_bodies_are_422_over_socket(self):
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                store = await client.post(
                    "/api/sdk/knowledge/store", json={}, headers=headers
                )
                search = await client.post(
                    "/api/sdk/knowledge/search",
                    json={"query": "q", "limit": "many"},
                    headers=headers,
                )
            assert store.status_code == 422, store.text
            assert search.status_code == 422, search.text
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_missing_document_get_is_404_over_socket(self):
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                response = await client.get(
                    "/api/sdk/knowledge/get",
                    params={
                        "key": f"absent-{uuid4().hex[:8]}",
                        "namespace": f"wsdk-ns-{uuid4().hex[:8]}",
                    },
                    headers=headers,
                )
            assert response.status_code == 404, response.text
            assert response.json()["detail"] == "Document not found"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_cross_org_scope_denied_over_socket(self, async_session_factory):
        from sqlalchemy import delete

        from src.core.security import create_access_token
        from src.models.orm.organizations import Organization as OrganizationModel

        async with async_session_factory() as session:
            org_a = OrganizationModel(
                name=f"wsdk-knowledge-a-{uuid4().hex[:8]}",
                is_active=True,
                is_provider=False,
                created_by="worker-sdk-http-test",
            )
            org_b = OrganizationModel(
                name=f"wsdk-knowledge-b-{uuid4().hex[:8]}",
                is_active=True,
                is_provider=False,
                created_by="worker-sdk-http-test",
            )
            session.add_all([org_a, org_b])
            await session.commit()
            org_a_id, org_b_id = org_a.id, org_b.id

        token = create_access_token(
            {
                "sub": str(uuid4()),
                "email": "user@example.com",
                "name": "User",
                "org_id": str(org_a_id),
            }
        )
        headers = {"Authorization": f"Bearer {token}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                denied = await client.get(
                    "/api/sdk/knowledge/get",
                    params={
                        "key": "k",
                        "namespace": "ns",
                        "scope": str(org_b_id),
                    },
                    headers=headers,
                )
                malformed = await client.post(
                    "/api/sdk/knowledge/store",
                    json={
                        "content": "x",
                        "namespace": "ns",
                        "scope": "not-a-uuid",
                    },
                    headers=headers,
                )
            assert denied.status_code == 403, denied.text
            assert malformed.status_code == 422, malformed.text
        finally:
            await server.stop()
            async with async_session_factory() as session:
                await session.execute(
                    delete(OrganizationModel).where(
                        OrganizationModel.id.in_([org_a_id, org_b_id])
                    )
                )
                await session.commit()


class TestEngineLocalKnowledgeFallback:
    """A failed knowledge socket request never replays over the network API."""

    @pytest.mark.asyncio
    async def test_knowledge_local_failure_does_not_fall_back_to_network(
        self, monkeypatch
    ):
        import bifrost.client as client_module
        from bifrost.knowledge import knowledge

        # This test proves there is no network fallback after a local
        # failure, not how long the transient backoff runs: collapse the
        # retry schedule so each request makes one local attempt.
        monkeypatch.setattr(client_module, "SDK_RETRY_BACKOFF_SECONDS", ())

        missing_socket = os.path.join(
            "/tmp", f"bifrost-missing-{uuid4().hex}.sock"
        )
        _install_engine_socket(missing_socket)
        _set_client(BifrostClient("http://dead-api", "token"))

        with pytest.raises(httpx.ConnectError):
            await knowledge.store("x", namespace="ns")
        with pytest.raises(httpx.ConnectError):
            await knowledge.store_many([{"content": "x"}], namespace="ns")
        with pytest.raises(httpx.ConnectError):
            await knowledge.search("q", namespace="ns")
        with pytest.raises(httpx.ConnectError):
            await knowledge.delete("k", namespace="ns")
        with pytest.raises(httpx.ConnectError):
            await knowledge.delete_namespace("ns")
        with pytest.raises(httpx.ConnectError):
            await knowledge.list_namespaces()
        with pytest.raises(httpx.ConnectError):
            await knowledge.get("k", namespace="ns")


class TestSocketWorkflowsAndExecutions:
    """Gate C5a: the socket serves the real workflow/execution routes."""

    @pytest.mark.asyncio
    async def test_workflow_list_requires_superuser(self):
        from src.core.security import create_access_token

        caller = {
            "Authorization": "Bearer "
            + create_access_token(
                {
                    "sub": str(uuid4()),
                    "email": "user@example.com",
                    "name": "User",
                    "org_id": str(uuid4()),
                }
            )
        }
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                denied = await client.get("/api/workflows", headers=caller)
                allowed = await client.get(
                    "/api/workflows",
                    headers={"Authorization": f"Bearer {_engine_token()}"},
                )
            assert denied.status_code == 403, denied.text
            assert allowed.status_code == 200, allowed.text
            assert isinstance(allowed.json(), list)
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_execution_foreign_is_403_over_socket(self, async_session_factory):
        from sqlalchemy import delete

        from src.core.security import create_access_token
        from src.models.enums import ExecutionStatus
        from src.models.orm.executions import Execution as ExecutionModel
        from src.models.orm.organizations import Organization as OrganizationModel
        from src.models.orm.users import User as UserModel

        owner_org = uuid4()
        async with async_session_factory() as session:
            org = OrganizationModel(
                id=owner_org,
                name=f"wsdk-exec-org-{uuid4().hex[:8]}",
                is_active=True,
                created_by="worker-sdk-http-test",
            )
            owner = UserModel(
                email=f"wsdk-exec-owner-{uuid4().hex[:8]}@example.com",
                name="Owner",
                organization_id=owner_org,
            )
            session.add_all([org, owner])
            await session.flush()
            row = ExecutionModel(
                workflow_name="wsdk-foreign-exec",
                status=ExecutionStatus.SUCCESS,
                parameters={},
                executed_by=owner.id,
                executed_by_name="Owner",
                organization_id=owner_org,
            )
            session.add(row)
            await session.commit()
            exec_id, owner_id = row.id, owner.id

        token = create_access_token(
            {
                "sub": str(uuid4()),
                "email": "other@example.com",
                "name": "Other",
                "org_id": str(uuid4()),
            }
        )
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                response = await client.get(
                    f"/api/executions/{exec_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert response.status_code == 403, response.text
        finally:
            await server.stop()
            async with async_session_factory() as session:
                await session.execute(
                    delete(ExecutionModel).where(ExecutionModel.id == exec_id)
                )
                await session.execute(
                    delete(UserModel).where(UserModel.id == owner_id)
                )
                await session.execute(
                    delete(OrganizationModel).where(
                        OrganizationModel.id == owner_org
                    )
                )
                await session.commit()

    @pytest.mark.asyncio
    async def test_execution_detail_missing_is_404_and_malformed_is_422(self):
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                missing = await client.get(
                    f"/api/executions/{uuid4()}", headers=headers
                )
                malformed = await client.get(
                    "/api/executions/not-a-uuid", headers=headers
                )
            assert missing.status_code == 404, missing.text
            assert malformed.status_code == 422, malformed.text
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_execution_list_rejects_unknown_query_param(self):
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                response = await client.get(
                    "/api/executions",
                    params={"bogus": "1"},
                    headers=headers,
                )
            assert response.status_code == 422, response.text
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_execute_malformed_body_is_422(self):
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                response = await client.post(
                    "/api/workflows/execute", json={}, headers=headers
                )
            assert response.status_code == 422, response.text
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_cancel_missing_is_404_and_malformed_is_422(self):
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                missing = await client.post(
                    f"/api/workflows/executions/{uuid4()}/cancel",
                    headers=headers,
                )
                malformed = await client.post(
                    "/api/workflows/executions/not-a-uuid/cancel",
                    headers=headers,
                )
            assert missing.status_code == 404, missing.text
            assert malformed.status_code == 422, malformed.text
        finally:
            await server.stop()


class TestEngineLocalWorkflowExecutionFallback:
    """A failed workflow/execution socket request never replays over the network."""

    @pytest.mark.asyncio
    async def test_workflow_execution_local_failure_does_not_fall_back(
        self, monkeypatch, async_session_factory
    ):
        import bifrost.client as client_module
        from bifrost.executions import executions
        from bifrost.workflows import workflows

        # Prove no network fallback, not how long the transient backoff runs:
        # collapse the retry schedule so each request makes one local attempt.
        monkeypatch.setattr(client_module, "SDK_RETRY_BACKOFF_SECONDS", ())

        missing_socket = os.path.join(
            "/tmp", f"bifrost-missing-{uuid4().hex}.sock"
        )
        _install_engine_socket(missing_socket)
        _set_client(BifrostClient("http://dead-api", "token"))

        with pytest.raises(httpx.ConnectError):
            await workflows.list()
        with pytest.raises(httpx.ConnectError):
            await workflows.execute("wf", {"x": 1})
        with pytest.raises(httpx.ConnectError):
            await workflows.cancel(str(uuid4()))
        with pytest.raises(httpx.ConnectError):
            await executions.list()
        with pytest.raises(httpx.ConnectError):
            await executions.get(str(uuid4()))


class TestSocketAgentRuns:
    """Gate C5b: the socket serves the real SDK agent-run routes."""

    @pytest.mark.asyncio
    async def test_enqueue_over_socket_reaches_real_route(
        self, async_session_factory
    ):
        from sqlalchemy import delete

        from src.models.orm.agents import Agent as AgentModel

        agent_name = f"wsdk-agent-{uuid4().hex[:8]}"
        async with async_session_factory() as session:
            agent = AgentModel(
                name=agent_name,
                system_prompt="Socket test agent.",
                is_active=True,
                created_by="worker-sdk-http-test",
            )
            session.add(agent)
            await session.commit()
            agent_id = agent.id

        run_id = str(uuid4())
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            # The route, auth, DTO validation, and shared service run for real;
            # only the broker-publishing leaf is stubbed so the unit lane does
            # not hand a run to a worker.
            with patch(
                "src.services.execution.agent_run_service.enqueue_agent_run",
                new=AsyncMock(return_value=run_id),
            ) as mock_enqueue:
                async with _socket_client(server) as client:
                    response = await client.post(
                        "/api/agent-runs/enqueue",
                        json={
                            "agent_name": agent_name,
                            "input": {"ticket_id": 1},
                        },
                        headers=headers,
                    )
            assert response.status_code == 202, response.text
            assert response.json() == {"run_id": run_id, "status": "queued"}
            assert mock_enqueue.await_count == 1
            kwargs = mock_enqueue.call_args.kwargs
            assert kwargs["agent_id"] == str(agent_id)
            assert kwargs["trigger_type"] == "api"
            assert kwargs["input_data"] == {"ticket_id": 1}
            assert kwargs["sync"] is False
        finally:
            await server.stop()
            async with async_session_factory() as session:
                await session.execute(
                    delete(AgentModel).where(AgentModel.id == agent_id)
                )
                await session.commit()

    @pytest.mark.asyncio
    async def test_live_read_over_socket_returns_detail(
        self, async_session_factory
    ):
        from sqlalchemy import delete, select

        from src.models.orm.agent_runs import AgentRun as AgentRunModel
        from src.models.orm.agents import Agent as AgentModel

        stem = f"wsdk-read-{uuid4().hex[:8]}"
        agent_name = f"{stem}-agent"
        async with async_session_factory() as session:
            agent = AgentModel(
                name=agent_name,
                system_prompt="Socket read agent.",
                is_active=True,
                created_by="worker-sdk-http-test",
            )
            session.add(agent)
            await session.flush()
            run = AgentRunModel(
                agent_id=agent.id,
                trigger_type="api",
                status="completed",
            )
            session.add(run)
            await session.commit()
            agent_id, run_id = agent.id, run.id

        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                response = await client.get(
                    f"/api/agent-runs/{run_id}",
                    headers={"Authorization": f"Bearer {_engine_token()}"},
                )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["id"] == str(run_id)
            assert payload["agent_name"] == agent_name
            assert payload["status"] == "completed"

            # The row is durable and visible to an independent session.
            async with async_session_factory() as check:
                row = (
                    await check.execute(
                        select(AgentRunModel).where(AgentRunModel.id == run_id)
                    )
                ).scalar_one_or_none()
            assert row is not None
        finally:
            await server.stop()
            async with async_session_factory() as session:
                await session.execute(
                    delete(AgentRunModel).where(AgentRunModel.id == run_id)
                )
                await session.execute(
                    delete(AgentModel).where(AgentModel.id == agent_id)
                )
                await session.commit()

    @pytest.mark.asyncio
    async def test_missing_run_and_agent_are_404_over_socket(self):
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                missing_run = await client.get(
                    f"/api/agent-runs/{uuid4()}", headers=headers
                )
                missing_agent = await client.post(
                    "/api/agent-runs/enqueue",
                    json={"agent_name": f"absent-{uuid4().hex[:8]}"},
                    headers=headers,
                )
            assert missing_run.status_code == 404, missing_run.text
            assert missing_agent.status_code == 404, missing_agent.text
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_malformed_bodies_are_422_over_socket(self):
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                malformed_id = await client.get(
                    "/api/agent-runs/not-a-uuid", headers=headers
                )
                malformed_body = await client.post(
                    "/api/agent-runs/enqueue", json={}, headers=headers
                )
            assert malformed_id.status_code == 422, malformed_id.text
            assert malformed_body.status_code == 422, malformed_body.text
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_auth_and_visibility_denied_over_socket(
        self, async_session_factory
    ):
        from sqlalchemy import delete

        from src.core.security import create_access_token
        from src.models.orm.agent_runs import AgentRun as AgentRunModel
        from src.models.orm.agents import Agent as AgentModel
        from src.models.orm.organizations import Organization as OrganizationModel

        async with async_session_factory() as session:
            org = OrganizationModel(
                name=f"wsdk-agent-org-{uuid4().hex[:8]}",
                is_active=True,
                is_provider=False,
                created_by="worker-sdk-http-test",
            )
            session.add(org)
            await session.flush()
            agent = AgentModel(
                name=f"wsdk-hidden-{uuid4().hex[:8]}",
                system_prompt="Hidden agent.",
                is_active=True,
                created_by="worker-sdk-http-test",
            )
            session.add(agent)
            await session.flush()
            run = AgentRunModel(
                agent_id=agent.id,
                trigger_type="api",
                status="completed",
                org_id=org.id,
            )
            session.add(run)
            await session.commit()
            org_id, agent_id, run_id = org.id, agent.id, run.id

        # A non-superuser outside the run's org must not see it.
        other_token = create_access_token(
            {
                "sub": str(uuid4()),
                "email": "other@example.com",
                "name": "Other",
                "org_id": str(uuid4()),
            }
        )
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                missing_auth = await client.get(f"/api/agent-runs/{run_id}")
                invalid_auth = await client.post(
                    "/api/agent-runs/enqueue",
                    json={"agent_name": "x"},
                    headers={"Authorization": "Bearer not-a-token"},
                )
                hidden = await client.get(
                    f"/api/agent-runs/{run_id}",
                    headers={"Authorization": f"Bearer {other_token}"},
                )
            assert missing_auth.status_code == 401, missing_auth.text
            assert invalid_auth.status_code == 401, invalid_auth.text
            assert hidden.status_code == 404, hidden.text
        finally:
            await server.stop()
            async with async_session_factory() as session:
                await session.execute(
                    delete(AgentRunModel).where(AgentRunModel.id == run_id)
                )
                await session.execute(
                    delete(AgentModel).where(AgentModel.id == agent_id)
                )
                await session.execute(
                    delete(OrganizationModel).where(
                        OrganizationModel.id == org_id
                    )
                )
                await session.commit()


class TestEngineLocalAgentsFallback:
    """A failed agent socket request never replays over the network API."""

    @pytest.mark.asyncio
    async def test_agents_local_failure_does_not_fall_back(self, monkeypatch):
        import importlib

        import bifrost.client as client_module

        agents_mod = importlib.import_module("bifrost.agents")

        # Prove no network fallback, not how long the transient backoff runs:
        # collapse the retry schedule so each request makes one local attempt.
        monkeypatch.setattr(client_module, "SDK_RETRY_BACKOFF_SECONDS", ())

        missing_socket = os.path.join(
            "/tmp", f"bifrost-missing-{uuid4().hex}.sock"
        )
        _install_engine_socket(missing_socket)
        _set_client(BifrostClient("http://dead-api", "token"))

        with pytest.raises(httpx.ConnectError):
            await agents_mod.agents.enqueue("A")
        with pytest.raises(httpx.ConnectError):
            await agents_mod.agents.get_run(str(uuid4()))


class TestSocketEventsForms:
    """Gate C5c: the socket serves the real event/form routes with their DTOs."""

    @pytest.mark.asyncio
    async def test_events_emit_over_socket_reaches_real_route(self):
        event_id = uuid4()
        topic = f"wsdk.events.{uuid4().hex[:8]}"
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                # The route, auth, DTO validation, and shared service run for
                # real; only the durable emitter leaf is stubbed so the unit
                # lane does not commit an event.
                with patch(
                    "src.services.events.emit_event",
                    new=AsyncMock(return_value=(event_id, 2)),
                ) as durable:
                    response = await client.post(
                        "/api/events/emit",
                        json={"topic": topic, "data": {"k": "v"}, "scope": None},
                        headers=headers,
                    )
            assert response.status_code == 200, response.text
            assert response.json() == {
                "event_id": str(event_id),
                "subscribers_notified": 2,
            }
            durable.assert_awaited_once()
            assert durable.call_args.args[0] == topic
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_events_emit_over_socket_stamps_scope_and_gates_solution(
        self, async_session_factory
    ):
        """Scope rides the body; an unknown/sealed Solution is a 404.

        Proves the socket route preserves the HTTP precedence: authorization
        (engine superuser) then topic validation then scope parsing then
        Solution resolution + inbound gate, with the durable emitter leaf
        stubbed so the unit lane commits no event.
        """
        from src.models.orm.organizations import Organization as OrganizationModel

        event_id = uuid4()
        topic = f"wsdk.events.scope.{uuid4().hex[:8]}"
        headers = {"Authorization": f"Bearer {_engine_token()}"}

        async with async_session_factory() as session:
            org = OrganizationModel(
                name=f"wsdk-events-org-{uuid4().hex[:8]}",
                is_active=True,
                is_provider=False,
                created_by="worker-sdk-http-test",
            )
            session.add(org)
            await session.commit()
            org_id = org.id

        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                with patch(
                    "src.services.events.emit_event",
                    new=AsyncMock(return_value=(event_id, 0)),
                ) as durable:
                    scoped = await client.post(
                        "/api/events/emit",
                        json={
                            "topic": topic,
                            "data": {"k": "v"},
                            "scope": str(org_id),
                        },
                        headers=headers,
                    )
                    unknown_solution = await client.post(
                        "/api/events/emit",
                        json={
                            "topic": topic,
                            "data": {},
                            "scope": str(org_id),
                            "solution": str(uuid4()),
                        },
                        headers=headers,
                    )
                    malformed_scope = await client.post(
                        "/api/events/emit",
                        json={"topic": topic, "data": {}, "scope": "nope"},
                        headers=headers,
                    )
                    invalid_topic = await client.post(
                        "/api/events/emit",
                        json={"topic": "NODOT", "data": {}, "scope": None},
                        headers=headers,
                    )
            assert scoped.status_code == 200, scoped.text
            assert durable.call_args.kwargs["organization_id"] == org_id
            assert durable.call_args.kwargs["solution_id"] is None
            # Unknown Solution resolves to None → 404, before the emit.
            assert unknown_solution.status_code == 404, unknown_solution.text
            # Malformed scope and invalid topic are 400/400 like the HTTP path.
            assert malformed_scope.status_code == 400, malformed_scope.text
            assert invalid_topic.status_code == 400, invalid_topic.text
            assert durable.await_count == 1
        finally:
            await server.stop()
            from sqlalchemy import delete

            async with async_session_factory() as session:
                await session.execute(
                    delete(OrganizationModel).where(
                        OrganizationModel.id == org_id
                    )
                )
                await session.commit()

    @pytest.mark.asyncio
    async def test_forms_list_and_get_over_socket(self, async_session_factory):
        from sqlalchemy import delete

        from src.models.enums import FormAccessLevel
        from src.models.orm.forms import Form as FormModel
        from src.models.orm.organizations import Organization as OrganizationModel

        stem = f"wsdk-form-{uuid4().hex[:8]}"
        async with async_session_factory() as session:
            org = OrganizationModel(
                name=f"{stem}-org",
                is_active=True,
                is_provider=False,
                created_by="worker-sdk-http-test",
            )
            session.add(org)
            await session.flush()
            form = FormModel(
                name=stem,
                access_level=FormAccessLevel.AUTHENTICATED,
                organization_id=org.id,
                is_active=True,
                created_by="worker-sdk-http-test",
            )
            session.add(form)
            await session.commit()
            org_id, form_id = org.id, form.id

        headers = {"Authorization": f"Bearer {_engine_token()}"}
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                listed = await client.get("/api/forms", headers=headers)
                detail = await client.get(f"/api/forms/{form_id}", headers=headers)
                missing = await client.get(f"/api/forms/{uuid4()}", headers=headers)
                malformed = await client.get("/api/forms/not-a-uuid", headers=headers)
                # The detail route's ``/{form_id}/logo`` sibling is not mounted.
                sibling = await client.get(
                    f"/api/forms/{form_id}/logo", headers=headers
                )
            assert listed.status_code == 200, listed.text
            names = {f["name"] for f in listed.json()}
            assert stem in names
            assert detail.status_code == 200, detail.text
            assert detail.json()["id"] == str(form_id)
            assert detail.json()["name"] == stem
            assert missing.status_code == 404, missing.text
            assert malformed.status_code == 422, malformed.text
            assert sibling.status_code == 404, sibling.text
        finally:
            await server.stop()
            async with async_session_factory() as session:
                await session.execute(
                    delete(FormModel).where(FormModel.id == form_id)
                )
                await session.execute(
                    delete(OrganizationModel).where(
                        OrganizationModel.id == org_id
                    )
                )
                await session.commit()

    @pytest.mark.asyncio
    async def test_events_and_forms_require_auth_over_socket(self):
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                missing_emit = await client.post(
                    "/api/events/emit",
                    json={"topic": "wsdk.a.b", "data": {}},
                )
                invalid_emit = await client.post(
                    "/api/events/emit",
                    json={"topic": "wsdk.a.b", "data": {}},
                    headers={"Authorization": "Bearer not-a-token"},
                )
                missing_list = await client.get("/api/forms")
                invalid_get = await client.get(
                    f"/api/forms/{uuid4()}",
                    headers={"Authorization": "Bearer not-a-token"},
                )
            assert missing_emit.status_code == 401, missing_emit.text
            assert invalid_emit.status_code == 401, invalid_emit.text
            assert missing_list.status_code == 401, missing_list.text
            assert invalid_get.status_code == 401, invalid_get.text
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_forms_cross_org_get_is_403_over_socket(
        self, async_session_factory
    ):
        from sqlalchemy import delete

        from src.core.security import create_access_token
        from src.models.enums import FormAccessLevel
        from src.models.orm.forms import Form as FormModel
        from src.models.orm.organizations import Organization as OrganizationModel

        stem = f"wsdk-form-foreign-{uuid4().hex[:8]}"
        async with async_session_factory() as session:
            org = OrganizationModel(
                name=f"{stem}-org",
                is_active=True,
                is_provider=False,
                created_by="worker-sdk-http-test",
            )
            session.add(org)
            await session.flush()
            form = FormModel(
                name=stem,
                access_level=FormAccessLevel.AUTHENTICATED,
                organization_id=org.id,
                is_active=True,
                created_by="worker-sdk-http-test",
            )
            session.add(form)
            await session.commit()
            org_id, form_id = org.id, form.id

        token = create_access_token(
            {
                "sub": str(uuid4()),
                "email": "other@example.com",
                "name": "Other",
                "org_id": str(uuid4()),
            }
        )
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                response = await client.get(
                    f"/api/forms/{form_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert response.status_code == 403, response.text
        finally:
            await server.stop()
            async with async_session_factory() as session:
                await session.execute(
                    delete(FormModel).where(FormModel.id == form_id)
                )
                await session.execute(
                    delete(OrganizationModel).where(
                        OrganizationModel.id == org_id
                    )
                )
                await session.commit()


class TestEngineLocalEventsFormsFallback:
    """A failed event/form socket request never replays over the network API."""

    @pytest.mark.asyncio
    async def test_events_forms_local_failure_does_not_fall_back(
        self, monkeypatch
    ):
        import importlib

        import bifrost.client as client_module

        events_mod = importlib.import_module("bifrost.events")
        forms_mod = importlib.import_module("bifrost.forms")

        # Prove no network fallback, not how long the transient backoff runs:
        # collapse the retry schedule so each request makes one local attempt.
        monkeypatch.setattr(client_module, "SDK_RETRY_BACKOFF_SECONDS", ())

        missing_socket = os.path.join(
            "/tmp", f"bifrost-missing-{uuid4().hex}.sock"
        )
        _install_engine_socket(missing_socket)
        _set_client(BifrostClient("http://dead-api", "token"))

        with pytest.raises(httpx.ConnectError):
            await events_mod.events.emit("a.b", {})
        with pytest.raises(httpx.ConnectError):
            await forms_mod.forms.list()
        with pytest.raises(httpx.ConnectError):
            await forms_mod.forms.get(str(uuid4()))


class TestSocketOrganizationsUsers:
    """Gate C5d: the socket serves the real organization/user routes."""

    @pytest.mark.asyncio
    async def test_organization_facade_over_socket(self, async_session_factory):
        from src.models.orm.organizations import Organization as OrganizationModel

        stem = f"wsdk-org-{uuid4().hex[:8]}"
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        created_id: str | None = None
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                created = await client.post(
                    "/api/organizations",
                    json={"name": stem, "domain": None, "is_active": True},
                    headers=headers,
                )
                assert created.status_code == 201, created.text
                created_id = created.json()["id"]

                detail = await client.get(
                    f"/api/organizations/{created_id}", headers=headers
                )
                assert detail.status_code == 200, detail.text
                assert detail.json()["name"] == stem

                listed = await client.get("/api/organizations", headers=headers)
                assert listed.status_code == 200, listed.text
                assert created_id in {o["id"] for o in listed.json()}

                updated = await client.patch(
                    f"/api/organizations/{created_id}",
                    json={"name": f"{stem}-renamed"},
                    headers=headers,
                )
                assert updated.status_code == 200, updated.text
                assert updated.json()["name"] == f"{stem}-renamed"

                deleted = await client.delete(
                    f"/api/organizations/{created_id}", headers=headers
                )
                assert deleted.status_code == 204, deleted.text

                # Soft delete: the detail still resolves, now inactive.
                after = await client.get(
                    f"/api/organizations/{created_id}", headers=headers
                )
                assert after.status_code == 200, after.text
                assert after.json()["is_active"] is False

                missing = await client.get(
                    f"/api/organizations/{uuid4()}", headers=headers
                )
                assert missing.status_code == 404, missing.text
                malformed = await client.get(
                    "/api/organizations/not-a-uuid", headers=headers
                )
                assert malformed.status_code == 422, malformed.text
        finally:
            await server.stop()
            if created_id is not None:
                from sqlalchemy import delete
                from uuid import UUID

                async with async_session_factory() as session:
                    await session.execute(
                        delete(OrganizationModel).where(
                            OrganizationModel.id == UUID(created_id)
                        )
                    )
                    await session.commit()

    @pytest.mark.asyncio
    async def test_user_facade_over_socket(self, async_session_factory):
        from sqlalchemy import delete

        from src.models.orm.organizations import Organization as OrganizationModel
        from src.models.orm.users import User as UserModel

        stem = f"wsdk-user-{uuid4().hex[:8]}"
        headers = {"Authorization": f"Bearer {_engine_token()}"}
        user_id: str | None = None
        org_id: str | None = None
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                org = await client.post(
                    "/api/organizations",
                    json={"name": f"{stem}-org", "domain": None, "is_active": True},
                    headers=headers,
                )
                assert org.status_code == 201, org.text
                org_id = org.json()["id"]

                created = await client.post(
                    "/api/users",
                    json={
                        "email": f"{stem}@example.com",
                        "name": "Socket User",
                        "is_superuser": False,
                        "is_active": True,
                        "organization_id": org_id,
                    },
                    headers=headers,
                )
                assert created.status_code == 201, created.text
                user_id = created.json()["id"]

                detail = await client.get(f"/api/users/{user_id}", headers=headers)
                assert detail.status_code == 200, detail.text
                assert detail.json()["email"] == f"{stem}@example.com"

                scoped = await client.get(
                    "/api/users", params={"scope": org_id}, headers=headers
                )
                assert scoped.status_code == 200, scoped.text
                assert user_id in {u["id"] for u in scoped.json()}

                updated = await client.patch(
                    f"/api/users/{user_id}",
                    json={"name": "Socket Renamed"},
                    headers=headers,
                )
                assert updated.status_code == 200, updated.text
                assert updated.json()["name"] == "Socket Renamed"

                malformed = await client.patch(
                    f"/api/users/{user_id}",
                    json={"is_active": "not-a-bool"},
                    headers=headers,
                )
                assert malformed.status_code == 422, malformed.text

                deleted = await client.delete(
                    f"/api/users/{user_id}", headers=headers
                )
                assert deleted.status_code == 204, deleted.text
                user_id = None

                missing = await client.get(f"/api/users/{uuid4()}", headers=headers)
                assert missing.status_code == 404, missing.text
        finally:
            await server.stop()
            async with async_session_factory() as session:
                from uuid import UUID

                if user_id is not None:
                    await session.execute(
                        delete(UserModel).where(UserModel.id == UUID(user_id))
                    )
                if org_id is not None:
                    await session.execute(
                        delete(OrganizationModel).where(
                            OrganizationModel.id == UUID(org_id)
                        )
                    )
                await session.commit()

    @pytest.mark.asyncio
    async def test_organizations_and_users_require_auth_over_socket(self):
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                missing_list = await client.get("/api/organizations")
                missing_create = await client.post(
                    "/api/organizations", json={"name": "nope"}
                )
                missing_user_list = await client.get("/api/users")
                invalid_get = await client.get(
                    f"/api/users/{uuid4()}",
                    headers={"Authorization": "Bearer not-a-token"},
                )
            assert missing_list.status_code == 401, missing_list.text
            assert missing_create.status_code == 401, missing_create.text
            assert missing_user_list.status_code == 401, missing_user_list.text
            assert invalid_get.status_code == 401, invalid_get.text
        finally:
            await server.stop()


class TestEngineLocalOrganizationsUsersFallback:
    """A failed org/user socket request never replays over the network API."""

    @pytest.mark.asyncio
    async def test_organizations_users_local_failure_does_not_fall_back(
        self, monkeypatch
    ):
        import importlib

        import bifrost.client as client_module

        organizations_mod = importlib.import_module("bifrost.organizations")
        users_mod = importlib.import_module("bifrost.users")

        # Prove no network fallback, not how long the transient backoff runs:
        # collapse the retry schedule so each request makes one local attempt.
        monkeypatch.setattr(client_module, "SDK_RETRY_BACKOFF_SECONDS", ())

        missing_socket = os.path.join(
            "/tmp", f"bifrost-missing-{uuid4().hex}.sock"
        )
        _install_engine_socket(missing_socket)
        _set_client(BifrostClient("http://dead-api", "token"))

        with pytest.raises(httpx.ConnectError):
            await organizations_mod.organizations.list()
        with pytest.raises(httpx.ConnectError):
            await organizations_mod.organizations.get(str(uuid4()))
        with pytest.raises(httpx.ConnectError):
            await users_mod.users.list()
        with pytest.raises(httpx.ConnectError):
            await users_mod.users.get(str(uuid4()))


class TestSocketRoles:
    """Gate C5e: the socket serves the real role routes."""

    @pytest.mark.asyncio
    async def test_role_facade_over_socket(self, async_session_factory):
        from sqlalchemy import delete
        from uuid import UUID

        from src.models.enums import FormAccessLevel
        from src.models.orm.forms import Form as FormModel
        from src.models.orm.users import Role as RoleModel
        from src.models.orm.users import User as UserModel

        stem = f"wsdk-role-{uuid4().hex[:8]}"
        headers = {"Authorization": f"Bearer {_engine_token()}"}

        # Roles validate their assignment targets, so seed a committed user
        # and form for the parent-served routes to find over their own session.
        async with async_session_factory() as seed:
            user = UserModel(
                email=f"{stem}@example.com",
                name="Socket Role User",
                is_active=True,
                is_superuser=True,
            )
            form = FormModel(
                name=f"{stem}-form",
                access_level=FormAccessLevel.AUTHENTICATED,
                organization_id=None,
                is_active=True,
                created_by="worker-sdk-http-test",
            )
            seed.add_all([user, form])
            await seed.commit()
            user_id, form_id = str(user.id), str(form.id)

        role_id: str | None = None
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                created = await client.post(
                    "/api/roles",
                    json={"name": stem, "description": "socket", "is_active": True},
                    headers=headers,
                )
                assert created.status_code == 201, created.text
                role_id = created.json()["id"]
                assert created.json()["name"] == stem

                detail = await client.get(f"/api/roles/{role_id}", headers=headers)
                assert detail.status_code == 200, detail.text
                assert detail.json()["description"] == "socket"

                listed = await client.get("/api/roles", headers=headers)
                assert listed.status_code == 200, listed.text
                assert role_id in {r["id"] for r in listed.json()}

                updated = await client.patch(
                    f"/api/roles/{role_id}",
                    json={"description": "socket-renamed"},
                    headers=headers,
                )
                assert updated.status_code == 200, updated.text
                assert updated.json()["description"] == "socket-renamed"

                assigned_users = await client.post(
                    f"/api/roles/{role_id}/users",
                    json={"user_ids": [user_id]},
                    headers=headers,
                )
                assert assigned_users.status_code == 204, assigned_users.text
                users_out = await client.get(
                    f"/api/roles/{role_id}/users", headers=headers
                )
                assert users_out.status_code == 200, users_out.text
                assert users_out.json()["user_ids"] == [user_id]

                assigned_forms = await client.post(
                    f"/api/roles/{role_id}/forms",
                    json={"form_ids": [form_id]},
                    headers=headers,
                )
                assert assigned_forms.status_code == 204, assigned_forms.text
                forms_out = await client.get(
                    f"/api/roles/{role_id}/forms", headers=headers
                )
                assert forms_out.status_code == 200, forms_out.text
                assert forms_out.json()["form_ids"] == [form_id]

                malformed_assign = await client.post(
                    f"/api/roles/{role_id}/users",
                    json={"user_ids": []},
                    headers=headers,
                )
                assert malformed_assign.status_code == 422, malformed_assign.text

                malformed_id = await client.get(
                    "/api/roles/not-a-uuid", headers=headers
                )
                assert malformed_id.status_code == 422, malformed_id.text

                deleted = await client.delete(
                    f"/api/roles/{role_id}", headers=headers
                )
                assert deleted.status_code == 204, deleted.text
                role_id = None

                missing = await client.get(
                    f"/api/roles/{uuid4()}", headers=headers
                )
                assert missing.status_code == 404, missing.text
        finally:
            await server.stop()
            async with async_session_factory() as cleanup:
                if role_id is not None:
                    await cleanup.execute(
                        delete(RoleModel).where(RoleModel.id == UUID(role_id))
                    )
                await cleanup.execute(
                    delete(FormModel).where(FormModel.name == f"{stem}-form")
                )
                await cleanup.execute(
                    delete(UserModel).where(
                        UserModel.email == f"{stem}@example.com"
                    )
                )
                await cleanup.commit()

    @pytest.mark.asyncio
    async def test_roles_require_auth_over_socket(self):
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            async with _socket_client(server) as client:
                missing_list = await client.get("/api/roles")
                missing_create = await client.post(
                    "/api/roles", json={"name": "nope"}
                )
                invalid_get = await client.get(
                    f"/api/roles/{uuid4()}",
                    headers={"Authorization": "Bearer not-a-token"},
                )
            assert missing_list.status_code == 401, missing_list.text
            assert missing_create.status_code == 401, missing_create.text
            assert invalid_get.status_code == 401, invalid_get.text
        finally:
            await server.stop()


class TestEngineLocalRolesFallback:
    """A failed role socket request never replays over the network API."""

    @pytest.mark.asyncio
    async def test_roles_local_failure_does_not_fall_back(self, monkeypatch):
        import importlib

        import bifrost.client as client_module

        roles_mod = importlib.import_module("bifrost.roles")

        # Prove no network fallback, not how long the transient backoff runs:
        # collapse the retry schedule so each request makes one local attempt.
        monkeypatch.setattr(client_module, "SDK_RETRY_BACKOFF_SECONDS", ())

        missing_socket = os.path.join(
            "/tmp", f"bifrost-missing-{uuid4().hex}.sock"
        )
        _install_engine_socket(missing_socket)
        _set_client(BifrostClient("http://dead-api", "token"))

        with pytest.raises(httpx.ConnectError):
            await roles_mod.roles.list()
        with pytest.raises(httpx.ConnectError):
            await roles_mod.roles.get(str(uuid4()))
        with pytest.raises(httpx.ConnectError):
            await roles_mod.roles.create("offline-role")


def _patch_ai_provider(monkeypatch, *, content="socket hello", delay=0.0):
    """Patch the provider leaf with a fake client; no real key is used."""
    from types import SimpleNamespace

    import src.services.llm as llm_pkg
    import src.services.llm.factory as llm_factory

    async def _complete(**_kwargs):
        if delay:
            await asyncio.sleep(delay)
        return SimpleNamespace(
            content=content,
            input_tokens=4,
            output_tokens=6,
            cache_read_tokens=0,
            cache_write_tokens=0,
            provider_cost=None,
            model="gpt-4o",
        )

    fake_client = AsyncMock()
    fake_client.provider_name = "openai"
    fake_client.model_name = "gpt-4o"
    fake_client.complete = AsyncMock(side_effect=_complete)
    monkeypatch.setattr(
        llm_pkg, "get_llm_client", AsyncMock(return_value=fake_client)
    )
    monkeypatch.setattr(
        llm_factory,
        "get_llm_config",
        AsyncMock(return_value=SimpleNamespace(provider="openai", model="gpt-4o")),
    )
    monkeypatch.setattr(
        "src.core.cache.get_shared_redis", AsyncMock(return_value=AsyncMock())
    )
    monkeypatch.setattr(
        "src.services.ai_usage_service.record_ai_usage", AsyncMock()
    )
    return fake_client


class TestSocketAI:
    """Gate C5f: the socket serves the real AI complete/info routes."""

    @pytest.mark.asyncio
    async def test_complete_and_info_over_socket(self, monkeypatch):
        from bifrost.ai import ai as ai_facade

        fake_client = _patch_ai_provider(monkeypatch)
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            _install_engine_socket(server.socket_path)  # type: ignore[arg-type]
            _set_client(
                BifrostClient("http://dead-api", _engine_token())
            )
            completed = await ai_facade.complete(
                "Hi",
                org_id=None,
                profile="Reasoning",
                model="gpt-4o",
                max_tokens=12,
            )
            info = await ai_facade.get_model_info()
        finally:
            await server.stop()

        assert completed.content == "socket hello"
        assert completed.model == "gpt-4o"
        assert completed.input_tokens == 4
        assert completed.output_tokens == 6
        assert info == {"provider": "openai", "model": "gpt-4o"}
        sent = fake_client.complete.await_args.kwargs
        assert sent["max_tokens"] == 12
        assert sent["model"] == "gpt-4o"
        assert sent["messages"][-1].content == "Hi"

    @pytest.mark.asyncio
    async def test_large_completion_body_over_socket(self, monkeypatch):
        """A large input file rides ordinary HTTP with no channel framing."""
        from bifrost.ai import ai as ai_facade
        from bifrost.models import AIInputFile

        fake_client = _patch_ai_provider(monkeypatch)
        blob = b"x" * 200_000
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            _install_engine_socket(server.socket_path)  # type: ignore[arg-type]
            _set_client(BifrostClient("http://dead-api", _engine_token()))
            completed = await ai_facade.complete(
                "Summarize this",
                files=[
                    AIInputFile(
                        filename="big.bin",
                        content_type="application/octet-stream",
                        data=blob,
                    )
                ],
            )
        finally:
            await server.stop()

        assert completed.content == "socket hello"
        sent = fake_client.complete.await_args.kwargs["messages"]
        assert sent[-1].input_files[0].data == blob

    @pytest.mark.asyncio
    async def test_slow_completion_ignores_client_default_deadline(self, monkeypatch):
        """Omitting ``timeout`` applies no SDK deadline (no 30s/patched default).

        The engine client's default is forced to a tiny value: only an
        explicitly forwarded ``timeout=None`` lets a slower provider finish.
        """
        from bifrost.ai import ai as ai_facade

        _patch_ai_provider(monkeypatch, content="slow hello", delay=0.3)
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            _install_engine_socket(server.socket_path)  # type: ignore[arg-type]
            client = BifrostClient("http://dead-api", _engine_token())
            _set_client(client)
            engine = client._get_engine_async_client()
            engine.timeout = httpx.Timeout(0.05)
            completed = await ai_facade.complete("Slow prompt")
        finally:
            await server.stop()

        assert completed.content == "slow hello"

    @pytest.mark.asyncio
    async def test_provider_error_over_socket_maps_to_public_runtime_error(
        self, monkeypatch
    ):
        """A provider failure on the socket route surfaces as public text.

        The real route maps the provider auth failure to a 401, and the
        facade re-raises the exact HTTP-parity ``RuntimeError`` instead of
        falling back to the dead network API.
        """
        from bifrost.ai import ai as ai_facade

        class AuthenticationError(Exception):
            pass

        AuthenticationError.__module__ = "openai"
        fake_client = _patch_ai_provider(monkeypatch)
        fake_client.complete = AsyncMock(
            side_effect=AuthenticationError("bad key")
        )
        server = WorkerSdkHttpServer()
        await server.start()
        try:
            _install_engine_socket(server.socket_path)  # type: ignore[arg-type]
            _set_client(BifrostClient("http://dead-api", _engine_token()))
            with pytest.raises(
                RuntimeError,
                match="AI completion failed: OpenAI API key is invalid",
            ):
                await ai_facade.complete("Hi")
        finally:
            await server.stop()


class TestEngineLocalAIFallback:
    """A failed AI socket request never replays over the network API."""

    @pytest.mark.asyncio
    async def test_ai_local_failure_does_not_fall_back(self, monkeypatch):
        import bifrost.client as client_module
        from bifrost.ai import ai as ai_facade

        # Prove no network fallback, not how long the transient backoff runs:
        # collapse the retry schedule so each request makes one local attempt.
        monkeypatch.setattr(client_module, "SDK_RETRY_BACKOFF_SECONDS", ())

        missing_socket = os.path.join(
            "/tmp", f"bifrost-missing-{uuid4().hex}.sock"
        )
        _install_engine_socket(missing_socket)
        _set_client(BifrostClient("http://dead-api", "token"))

        with pytest.raises(httpx.ConnectError):
            await ai_facade.complete("offline")
        with pytest.raises(httpx.ConnectError):
            await ai_facade.get_model_info()
