"""Worker-local HTTP surface for the engine SDK (Gate A proof).

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
# mounts the whole config facade even though only ``config.get`` is wired to
# the child transport yet.
CONFIG_ROUTE_PATHS: frozenset[str] = frozenset(
    {
        "/api/sdk/config/get",
        "/api/sdk/config/set",
        "/api/sdk/config/list",
        "/api/sdk/config/delete",
    }
)


def build_worker_sdk_app() -> Any:
    """Build a minimal ASGI app mounting only the existing config routes.

    Fails loudly if the router no longer exposes every expected path: a
    silently incomplete mount would leave engine SDK calls falling through
    to HTTP without the proof noticing.
    """
    from fastapi import FastAPI

    from src.routers.cli import router as sdk_router

    app = FastAPI(
        title="Bifrost worker-local engine SDK",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    selected = 0
    for route in sdk_router.routes:
        if getattr(route, "path", None) in CONFIG_ROUTE_PATHS:
            # Reuse the exact registered APIRoute (endpoint, dependencies,
            # response model) — never a re-created or copied handler.
            app.router.routes.append(route)
            selected += 1
    if selected != len(CONFIG_ROUTE_PATHS):
        raise RuntimeError(
            "worker-local SDK app could not mount every config route "
            f"({selected}/{len(CONFIG_ROUTE_PATHS)}); route selection is stale"
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
