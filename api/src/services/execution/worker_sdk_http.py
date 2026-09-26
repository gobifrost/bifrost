"""Worker-local HTTP surface for the engine SDK.

The worker parent owns pooled PostgreSQL and protected credentials; execution
children own neither. This module lets the worker parent serve a small set of
the **existing** SDK-facing FastAPI routes on a private Unix-domain socket so
engine children can make ordinary HTTP calls to them.

Route reuse, not reimplementation: the app below selects the real
``APIRoute`` objects out of ``src.routers.cli.router`` by path and mounts
them unchanged, so the endpoint functions, auth dependency (ordinary engine
bearer token), request/response DTOs, shared services, status codes, and the
worker's already-initialized database engine are all the API's. No full API
app is started and no handler or service is copied.

Gate A proved the approach with the config routes; Gate C1 wired the config
facade to the shared client transport, Gate C2 added the six integrations
routes, Gate C3a added the table-definition routes (create/list) and the
document reads (get/query/count), Gate C3b adds the table mutations and
batch writes (insert/upsert/update/delete_document/batch/batch-delete plus
the auto-create POST /api/tables helper), Gate C4a adds the files facade
routes (read/write/list/delete/stat/exists/signed-url/search), Gate C4b adds
the artifact facade routes plus the durable platform-job status route that
``artifacts.create_video`` polls, Gate C4c adds the knowledge facade
routes (store/store-many/search/delete/get/delete_namespace/list_namespaces),
Gate C5a adds the workflow facade routes
(list/execute/cancel) and execution-history routes (list/get), Gate C5b
adds the SDK agent-run routes (enqueue/get), and Gate C5c adds the topic
emit route (``events.emit``) plus the form read routes (``forms.list`` /
``forms.get``). Gate C5d adds the organization facade routes
(create/get/list/update/delete) and the user facade routes
(list/get/create/update/delete). Gate C5e adds the role facade routes
(create/get/list/update/delete plus the user and form assignment reads and
writes). Streams and other domains stay on their existing channel path
until their own Gate C slice migrates them.

``import fastapi``/``uvicorn`` happen inside the functions so the worker
entry closure stays free of those heavyweights at import time (see
``tests/unit/test_import_hygiene.py``).

The selected routes need no API lifespan or ``app.state``: authentication
is a stateless bearer-token decode and database access resolves through the
worker's already-initialized global session factory. The server therefore
runs with ``lifespan="off"`` and does not need a full API instance.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

# The existing SDK-facing config routes served on the socket. Selected by
# path from the real router; the endpoint objects are reused as-is. Gate A
# mounts the whole config facade; Gate C1 wired all four methods
# (get/set/list/delete) to the shared client transport.
CONFIG_ROUTE_PATHS: frozenset[str] = frozenset(
    {
        "/api/sdk/config/get",
        "/api/sdk/config/set",
        "/api/sdk/config/list",
        "/api/sdk/config/delete",
    }
)

# Gate C2: the six existing integrations facade routes
# (get/list_mappings/get_mapping/upsert_mapping/delete_mapping plus the
# OAuth refresh used by ``OAuthCredentials.refresh``). Mounted by identity
# from the same router, exactly like the config routes.
INTEGRATION_ROUTE_PATHS: frozenset[str] = frozenset(
    {
        "/api/sdk/integrations/get",
        "/api/sdk/integrations/list_mappings",
        "/api/sdk/integrations/get_mapping",
        "/api/sdk/integrations/upsert_mapping",
        "/api/sdk/integrations/delete_mapping",
        "/api/sdk/integrations/refresh_token",
    }
)

# Gate C3a: the table-definition facade routes (create/list) selected from
# the cli SDK router, exactly like config and integrations. Delete rides the
# tables REST router below, which is where DELETE /api/tables/{table_id}
# lives.
TABLE_SDK_ROUTE_PATHS: frozenset[str] = frozenset(
    {
        "/api/sdk/tables/create",
        "/api/sdk/tables/list",
    }
)

# Gate C3a/C3b: the table-definition delete plus every document read and
# write served by the tables REST router. Keyed by path AND the exact
# methods needed, because ``/api/tables/{table_id}`` is also a GET/PATCH
# metadata route and ``/api/tables/{table_id}/documents/{doc_id}`` carries
# GET/PATCH/DELETE, and we must not accidentally mount an unrelated sibling.
# The shared ``BifrostClient.engine_request`` calls each one with the
# ordinary verb.
TABLE_ROUTE_METHODS: dict[str, frozenset[str]] = {
    # Auto-create-on-insert (loose table ensure helper).
    "/api/tables": frozenset({"POST"}),
    "/api/tables/{table_id}": frozenset({"DELETE"}),
    # Document writes.
    "/api/tables/{table_id}/documents": frozenset({"POST"}),
    "/api/tables/{table_id}/documents/upsert": frozenset({"POST"}),
    # Document reads.
    "/api/tables/{table_id}/documents/count": frozenset({"GET"}),
    "/api/tables/{table_id}/documents/{doc_id}": frozenset({"GET", "PATCH", "DELETE"}),
    "/api/tables/{table_id}/documents/query": frozenset({"POST"}),
    # Batch writes.
    "/api/tables/{table_id}/documents/batch": frozenset({"POST"}),
    "/api/tables/{table_id}/documents/batch-delete": frozenset({"POST"}),
}

# Gate C4a: the existing files facade routes. All eight live in
# ``src.routers.files`` (path-selected exactly, like the config and
# integrations routes). They carry the ordinary ``CurrentActiveUser``
# auth, the shared ``shared.sdk_files`` service, and the worker's
# initialized DB engine; the child needs no S3 credential.
FILES_ROUTE_PATHS: frozenset[str] = frozenset(
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

# Gate C4c: the existing knowledge facade routes, selected from the cli SDK
# router by exact path (each path owns one method, so no method filter is
# needed). They carry the ordinary ``CurrentUser`` auth, the shared
# ``shared.sdk_knowledge`` service, and the worker's initialized DB engine;
# embedding runs in the parent, so the child needs no provider credential.
KNOWLEDGE_ROUTE_PATHS: frozenset[str] = frozenset(
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

# Gate C4b: the existing artifact facade routes, selected from the cli SDK
# router by path AND method because ``/api/sdk/artifacts`` carries both GET
# (list) and POST (write). They carry the ordinary ``CurrentUser`` auth, the
# shared artifact services, and the worker's initialized DB engine; the
# child needs no storage/provider credential.
ARTIFACT_ROUTE_METHODS: dict[str, frozenset[str]] = {
    "/api/sdk/artifacts": frozenset({"GET", "POST"}),
    "/api/sdk/artifacts/document": frozenset({"POST"}),
    "/api/sdk/artifacts/spreadsheet": frozenset({"POST"}),
    "/api/sdk/artifacts/text": frozenset({"POST"}),
    "/api/sdk/artifacts/image": frozenset({"POST"}),
    "/api/sdk/artifacts/video": frozenset({"POST"}),
    "/api/sdk/artifacts/{artifact_id}/content": frozenset({"GET"}),
    "/api/sdk/artifacts/{artifact_id}/download-url": frozenset({"GET"}),
}
ARTIFACT_ROUTE_PATHS: frozenset[str] = frozenset(ARTIFACT_ROUTE_METHODS)

# Gate C4b: ``artifacts.create_video`` polls its durable platform job over
# the same socket. Mount only the single-job GET from the real
# ``src.routers.platform_jobs`` router; the list and cancel siblings stay
# on the API.
PLATFORM_JOB_ROUTE_METHODS: dict[str, frozenset[str]] = {
    "/api/platform-jobs/{job_id}": frozenset({"GET"}),
}

# Gate C5a: the workflow facade routes, selected from ``src.routers.workflows``
# by path AND method. They carry the ordinary ``CurrentSuperuser`` /
# ``CurrentActiveUser`` auth, the ``Context`` execution context, the shared
# ``shared.sdk_workflow_execution`` service, and the worker's initialized DB
# engine and queue. ``workflows.get`` is not here — it delegates to the
# execution-detail route below.
WORKFLOW_ROUTE_METHODS: dict[str, frozenset[str]] = {
    "/api/workflows": frozenset({"GET"}),
    "/api/workflows/execute": frozenset({"POST"}),
    "/api/workflows/executions/{execution_id}/cancel": frozenset({"POST"}),
}

# Gate C5a: the execution-history facade routes, selected from
# ``src.routers.executions`` by path AND method. ``/api/executions`` is the
# list route; ``/api/executions/{execution_id}`` is the detail route. Their
# ``/logs``, ``/{execution_id}/result``, and ``/{execution_id}/variables``
# siblings stay on the API.
EXECUTION_ROUTE_METHODS: dict[str, frozenset[str]] = {
    "/api/executions": frozenset({"GET"}),
    "/api/executions/{execution_id}": frozenset({"GET"}),
}

# Gate C5b: the SDK agent-run facade routes, selected from
# ``src.routers.agent_runs`` by path AND method. ``/api/agent-runs/{run_id}``
# owns only the detail GET here; the rerun/cancel/verdict/flag siblings are
# separate paths and stay on the API.
AGENT_RUN_ROUTE_METHODS: dict[str, frozenset[str]] = {
    "/api/agent-runs/enqueue": frozenset({"POST"}),
    "/api/agent-runs/{run_id}": frozenset({"GET"}),
}

# Gate C5c: the SDK event route, selected from ``src.routers.events`` by path
# AND method. ``/api/events/emit`` is the only events route the SDK calls; the
# source/subscription/event CRUD routes stay on the API.
EVENT_ROUTE_METHODS: dict[str, frozenset[str]] = {
    "/api/events/emit": frozenset({"POST"}),
}

# Gate C5c: the SDK form read routes, selected from ``src.routers.forms`` by
# path AND method. ``/api/forms`` is the list route and ``/api/forms/{form_id}``
# is the detail GET; the form mutation/publication/runtime/logo siblings and the
# ``/{form_id}/...`` child paths stay on the API.
FORM_ROUTE_METHODS: dict[str, frozenset[str]] = {
    "/api/forms": frozenset({"GET"}),
    "/api/forms/{form_id}": frozenset({"GET"}),
}

# Gate C5d: the SDK organization facade routes, selected from
# ``src.routers.organizations`` by path AND method. The list and create share
# ``/api/organizations``; the get/update/delete share
# ``/api/organizations/{org_id}``. No sibling paths are mounted.
ORGANIZATION_ROUTE_METHODS: dict[str, frozenset[str]] = {
    "/api/organizations": frozenset({"GET", "POST"}),
    "/api/organizations/{org_id}": frozenset({"GET", "PATCH", "DELETE"}),
}

# Gate C5d: the SDK user facade routes, selected from ``src.routers.users`` by
# path AND method. The list and create share ``/api/users``; the
# get/update/delete share ``/api/users/{user_id}``. The bulk, roles, forms,
# invite, and password siblings (including the ``/{user_id}/...`` child paths)
# stay on the API.
USER_ROUTE_METHODS: dict[str, frozenset[str]] = {
    "/api/users": frozenset({"GET", "POST"}),
    "/api/users/{user_id}": frozenset({"GET", "PATCH", "DELETE"}),
}

# Gate C5e: the SDK role facade routes, selected from ``src.routers.roles`` by
# path AND method. The list and create share ``/api/roles``; the
# get/update/delete share ``/api/roles/{role_id}``; the user and form
# assignment reads/writes share ``/api/roles/{role_id}/users`` and
# ``/api/roles/{role_id}/forms``. The PUT compatibility alias, the per-id
# remove, the bulk-unassign, and the agents/apps/workflows/knowledge siblings
# stay on the API.
ROLES_ROUTE_METHODS: dict[str, frozenset[str]] = {
    "/api/roles": frozenset({"GET", "POST"}),
    "/api/roles/{role_id}": frozenset({"GET", "PATCH", "DELETE"}),
    "/api/roles/{role_id}/users": frozenset({"GET", "POST"}),
    "/api/roles/{role_id}/forms": frozenset({"GET", "POST"}),
}

# Every route path this worker-local app serves from the cli SDK router.
# Other SDK domains keep their existing channel path until their Gate C
# slice migrates them.
SDK_ROUTE_PATHS: frozenset[str] = (
    CONFIG_ROUTE_PATHS
    | INTEGRATION_ROUTE_PATHS
    | TABLE_SDK_ROUTE_PATHS
    | ARTIFACT_ROUTE_PATHS
    | KNOWLEDGE_ROUTE_PATHS
)


def build_worker_sdk_app() -> Any:
    """Build a minimal ASGI app mounting only the existing SDK routes.

    Fails loudly if the router no longer exposes every expected path: a
    silently incomplete mount would leave engine SDK calls falling through
    to HTTP without the proof noticing.
    """
    from fastapi import FastAPI

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

    app = FastAPI(
        title="Bifrost worker-local engine SDK",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # The config, integrations, table-definition, and knowledge facade paths
    # are unique, so they are selected by exact path. Artifact routes are
    # selected by (path, method) because ``/api/sdk/artifacts`` is both GET
    # and POST.
    cli_path_only = (
        CONFIG_ROUTE_PATHS
        | INTEGRATION_ROUTE_PATHS
        | TABLE_SDK_ROUTE_PATHS
        | KNOWLEDGE_ROUTE_PATHS
    )
    selected = 0
    for route in sdk_router.routes:
        path = getattr(route, "path", None)
        if path in cli_path_only:
            # Reuse the exact registered APIRoute (endpoint, dependencies,
            # response model) — never a re-created or copied handler.
            app.router.routes.append(route)
            selected += 1
        elif path in ARTIFACT_ROUTE_METHODS:
            methods = getattr(route, "methods", None) or frozenset()
            if methods & ARTIFACT_ROUTE_METHODS[path]:
                app.router.routes.append(route)
                selected += 1
    # Each wanted method is one registered APIRoute, so the expected count is
    # the total number of (path, method) pairs for artifacts.
    expected = len(cli_path_only) + sum(
        len(methods) for methods in ARTIFACT_ROUTE_METHODS.values()
    )

    # Files facade routes are selected by exact path from the files router.
    # Their path strings are unique, so no method filter is needed.
    for route in files_router.routes:
        if getattr(route, "path", None) in FILES_ROUTE_PATHS:
            app.router.routes.append(route)
            selected += 1
    # Files paths are unique (one route each), so the expected count is simply
    # the number of paths.
    expected += len(FILES_ROUTE_PATHS)

    # Tables REST routes are selected by (path, method) so a shared path like
    # ``/api/tables/{table_id}`` does not drag in its unrelated GET/PATCH
    # siblings. Router order is preserved, so ``/documents/count`` is
    # registered before ``/documents/{doc_id}`` exactly as in the API.
    for route in tables_router.routes:
        wanted = TABLE_ROUTE_METHODS.get(getattr(route, "path", None), frozenset())
        methods = getattr(route, "methods", None) or frozenset()
        if wanted and methods & wanted:
            app.router.routes.append(route)
            selected += 1
    # Each wanted method is one registered APIRoute, so the expected count is
    # the total number of (path, method) pairs, not the number of paths.
    expected += sum(len(methods) for methods in TABLE_ROUTE_METHODS.values())

    # The durable platform-job status route ``artifacts.create_video`` polls
    # is mounted as the exact registered object, like every other route.
    for route in platform_jobs_router.routes:
        wanted = PLATFORM_JOB_ROUTE_METHODS.get(
            getattr(route, "path", None), frozenset()
        )
        methods = getattr(route, "methods", None) or frozenset()
        if wanted and methods & wanted:
            app.router.routes.append(route)
            selected += 1
    expected += sum(
        len(methods) for methods in PLATFORM_JOB_ROUTE_METHODS.values()
    )

    # Workflow and execution facade routes are selected by (path, method) from
    # their real routers. The shared ``/api/executions/{execution_id}`` path
    # has result/variables GET siblings, and the workflows router carries an
    # unrelated ``/usage-stats`` GET, so the exact method set matters.
    for route in workflows_router.routes:
        wanted = WORKFLOW_ROUTE_METHODS.get(
            getattr(route, "path", None), frozenset()
        )
        methods = getattr(route, "methods", None) or frozenset()
        if wanted and methods & wanted:
            app.router.routes.append(route)
            selected += 1
    expected += sum(
        len(methods) for methods in WORKFLOW_ROUTE_METHODS.values()
    )

    for route in executions_router.routes:
        wanted = EXECUTION_ROUTE_METHODS.get(
            getattr(route, "path", None), frozenset()
        )
        methods = getattr(route, "methods", None) or frozenset()
        if wanted and methods & wanted:
            app.router.routes.append(route)
            selected += 1
    expected += sum(
        len(methods) for methods in EXECUTION_ROUTE_METHODS.values()
    )

    # The SDK agent-run routes are selected by (path, method) from their real
    # router. ``/api/agent-runs/{run_id}`` is the detail GET; the rerun,
    # cancel, verdict, and flag-conversation siblings live on child paths and
    # stay on the API.
    for route in agent_runs_router.routes:
        wanted = AGENT_RUN_ROUTE_METHODS.get(
            getattr(route, "path", None), frozenset()
        )
        methods = getattr(route, "methods", None) or frozenset()
        if wanted and methods & wanted:
            app.router.routes.append(route)
            selected += 1
    expected += sum(
        len(methods) for methods in AGENT_RUN_ROUTE_METHODS.values()
    )

    # The SDK event and form facade routes are selected by (path, method) from
    # their real routers. The events router also carries the source/
    # subscription/event CRUD paths; the forms router carries mutations, the
    # publication/runtime/logo routes, and ``/{form_id}/...`` child paths. Only
    # the emit POST and the two form reads are mounted.
    for route in events_router.routes:
        wanted = EVENT_ROUTE_METHODS.get(
            getattr(route, "path", None), frozenset()
        )
        methods = getattr(route, "methods", None) or frozenset()
        if wanted and methods & wanted:
            app.router.routes.append(route)
            selected += 1
    expected += sum(
        len(methods) for methods in EVENT_ROUTE_METHODS.values()
    )

    for route in forms_router.routes:
        wanted = FORM_ROUTE_METHODS.get(
            getattr(route, "path", None), frozenset()
        )
        methods = getattr(route, "methods", None) or frozenset()
        if wanted and methods & wanted:
            app.router.routes.append(route)
            selected += 1
    expected += sum(
        len(methods) for methods in FORM_ROUTE_METHODS.values()
    )

    # The SDK organization and user facade routes are selected by
    # (path, method) from their real routers. Both routers share one path
    # across several methods, and the users router carries bulk/roles/forms/
    # invite/password siblings (including ``/{user_id}/...`` child paths), so
    # the exact method set matters.
    for route in organizations_router.routes:
        wanted = ORGANIZATION_ROUTE_METHODS.get(
            getattr(route, "path", None), frozenset()
        )
        methods = getattr(route, "methods", None) or frozenset()
        if wanted and methods & wanted:
            app.router.routes.append(route)
            selected += 1
    expected += sum(
        len(methods) for methods in ORGANIZATION_ROUTE_METHODS.values()
    )

    for route in users_router.routes:
        wanted = USER_ROUTE_METHODS.get(
            getattr(route, "path", None), frozenset()
        )
        methods = getattr(route, "methods", None) or frozenset()
        if wanted and methods & wanted:
            app.router.routes.append(route)
            selected += 1
    expected += sum(
        len(methods) for methods in USER_ROUTE_METHODS.values()
    )

    # The SDK role facade routes are selected by (path, method) from their
    # real router. The same path carries several methods, and the roles
    # router also holds the PUT alias, per-id and bulk-unassign DELETEs, and
    # the agents/apps/workflows/knowledge siblings, so the exact method set
    # matters.
    for route in roles_router.routes:
        wanted = ROLES_ROUTE_METHODS.get(
            getattr(route, "path", None), frozenset()
        )
        methods = getattr(route, "methods", None) or frozenset()
        if wanted and methods & wanted:
            app.router.routes.append(route)
            selected += 1
    expected += sum(
        len(methods) for methods in ROLES_ROUTE_METHODS.values()
    )

    if selected != expected:
        raise RuntimeError(
            "worker-local SDK app could not mount every SDK route "
            f"({selected}/{expected}); route selection is stale"
        )
    return app


@contextlib.contextmanager
def _no_signal_capture():
    """No-op replacement for uvicorn's signal capture.

    The worker owns process signal handling (``loop.add_signal_handler`` for
    SIGINT/SIGTERM). Uvicorn's ``capture_signals`` would install its own
    ``signal.signal`` handlers for the lifetime of the server and pre-empt
    the worker's graceful shutdown.
    """
    yield


class WorkerSdkHttpServer:
    """Serve the existing SDK config routes on a private Unix socket.

    Lifecycle: :meth:`start` creates a private 0700 directory with a 0600
    socket and runs uvicorn in-process on the current event loop (so the
    worker's pooled database engine and its loop remain usable).
    :meth:`stop` asks uvicorn to exit and unlinks the socket.
    """

    def __init__(self, socket_dir: str | None = None) -> None:
        self._socket_dir = socket_dir
        self._made_dir = False
        self._socket_path: str | None = None
        self._socket: socket.socket | None = None
        self._server: Any = None
        self._task: asyncio.Task[None] | None = None

    @property
    def socket_path(self) -> str | None:
        """Path children connect to, or None before start / after stop."""
        return self._socket_path

    async def start(self) -> None:
        """Bind the socket and begin serving; waits for the server to start."""
        if self._task is not None:
            return

        import uvicorn

        app = build_worker_sdk_app()

        socket_dir = self._socket_dir or tempfile.mkdtemp(
            prefix="bifrost-engine-sdk-"
        )
        self._made_dir = self._socket_dir is None
        socket_path = os.path.join(socket_dir, "engine.sock")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            os.chmod(socket_dir, 0o700)
            sock.bind(socket_path)
            # Private to the worker and its forked children (same user).
            os.chmod(socket_path, 0o600)
            sock.listen(128)
            sock.setblocking(False)
        except BaseException:
            sock.close()
            with contextlib.suppress(FileNotFoundError):
                os.unlink(socket_path)
            if self._made_dir:
                with contextlib.suppress(OSError):
                    os.rmdir(socket_dir)
                self._made_dir = False
            raise

        config = uvicorn.Config(
            app,
            uds=socket_path,
            lifespan="off",
            log_level="warning",
            log_config=None,
            access_log=False,
        )
        server = uvicorn.Server(config)
        # Worker owns signals; see :func:`_no_signal_capture`.
        server.capture_signals = _no_signal_capture  # type: ignore[method-assign]

        self._socket = sock
        self._socket_path = socket_path
        self._server = server
        self._task = asyncio.create_task(
            server.serve(sockets=[sock]), name="worker-sdk-http"
        )
        try:
            await self._await_started(server)
        except BaseException:
            await self.stop()
            raise
        logger.info("Worker-local engine SDK serving on %s", socket_path)

    async def _await_started(self, server: Any) -> None:
        task = self._task
        assert task is not None
        for _ in range(1000):
            if server.started:
                return
            if task.done():
                raise RuntimeError(
                    "worker-local engine SDK server failed to start: "
                    f"{task.exception()!r}"
                )
            await asyncio.sleep(0.01)
        raise RuntimeError("worker-local engine SDK server did not start in time")

    async def stop(self) -> None:
        """Stop serving and remove the socket. Idempotent."""
        server = self._server
        self._server = None
        if server is not None:
            server.should_exit = True

        task = self._task
        self._task = None
        if task is not None:
            with contextlib.suppress(Exception):
                await task

        # ``serve(sockets=[sock])`` closes the socket once it has taken it;
        # on a failed start it may never have, so close our copy too.
        if self._socket is not None:
            with contextlib.suppress(OSError):
                self._socket.close()
            self._socket = None
        path = self._socket_path
        self._socket_path = None
        if path is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(path)
            if self._made_dir:
                with contextlib.suppress(OSError):
                    os.rmdir(os.path.dirname(path))
                self._made_dir = False
        logger.info("Worker-local engine SDK server stopped")
