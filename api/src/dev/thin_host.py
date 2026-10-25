"""Experimental API-hosted execution control, explicitly selected in tests.

The bootstrap file grants one physical launcher a control connection. This is
an isolated-stack enrollment mechanism, not production Kubernetes admission.
Normal API startup does not import or expose this module.
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect

from src.main import app
from src.jobs.consumers.package_install import PackageInstallConsumer
from src.jobs.schedulers.platform_jobs import claim_platform_job, run_claimed_platform_job
from src.services.memory_requests import MemoryBudget, measure_budget, under_pressure
from src.services.worker_pool.claims import register_static_worker
from src.worker.admission import Admission
from src.worker.pool_main import PoolWorker


class RemoteProcess:
    def __init__(self, bridge: Bridge, route_id: str) -> None:
        self.bridge = bridge
        self.route_id = route_id
        self.returncode: int | None = None
        self.exited: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def wait(self) -> int:
        return await asyncio.shield(self.exited)

    async def terminate(self) -> None:
        if self.returncode is None:
            await self.bridge.request("terminate_job", {"route_id": self.route_id})
            await self.wait()


class Bridge:
    """Ephemeral command transport; PostgreSQL remains the durable authority."""
    def __init__(self, socket: WebSocket, hello: dict[str, Any]) -> None:
        self.socket = socket
        self.connected = True
        self.current = int(hello["current"])
        self.limit = int(hello["limit"])
        self.hostname = hello["hostname"]
        self.sample_at = time.monotonic()
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.jobs: dict[str, RemoteProcess] = {}
        self.pool: Any = None
        self.requirements = hello["requirements"]
        self.results: set[asyncio.Task[Any]] = set()
        self.lock = asyncio.Lock()

    def memory(self) -> tuple[int, int]:
        if not self.connected or time.monotonic() - self.sample_at > 3:
            return -1, self.limit
        return self.current, self.limit

    async def request(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.connected:
            raise ConnectionError("Launcher disconnected")
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            async with self.lock:
                await self.socket.send_text(json.dumps(
                    {"id": request_id, "action": action, "payload": payload}, default=str,
                ))
            # Package installation uses the existing long bounded pip calls.
            async with asyncio.timeout(600 if action in ("package_tool", "recycle") else 15):
                return await asyncio.shield(future)
        finally:
            self.pending.pop(request_id, None)

    async def spawn(self, claim: Any) -> RemoteProcess:
        route_id = str(uuid4())
        process = RemoteProcess(self, route_id)
        self.jobs[route_id] = process
        try:
            await self.request("platform_job", {"route_id": route_id,
                               "job_id": str(claim.id), "lease_token": str(claim.lease_token)})
        except BaseException:
            self.jobs.pop(route_id, None)
            raise
        return process

    async def receive(self, event: dict[str, Any]) -> None:
        if "reply" in event:
            future = self.pending.get(event["reply"])
            if future is not None and not future.done():
                if "error" in event:
                    error = MemoryError if event.get("error_type") == "MemoryError" else RuntimeError
                    future.set_exception(error(event["error"]))
                else:
                    future.set_result(event["result"])
        elif event.get("event") == "sample":
            self.current, self.limit = int(event["current"]), int(event["limit"])
            self.sample_at = time.monotonic()
            if self.pool is not None:
                await self.pool.handle_event(event)
        elif event.get("event") == "job_exit":
            process = self.jobs.pop(event["route_id"], None)
            if process is not None:
                process.returncode = int(event["returncode"])
                if not process.exited.done():
                    process.exited.set_result(process.returncode)
        else:
            # Completion may call SDK/Redis/DB or send a new command. Never
            # block this receiver, which must also deliver command replies.
            task = asyncio.create_task(self.pool.handle_event(event))
            self.results.add(task)
            task.add_done_callback(self.results.discard)
            task.add_done_callback(_observe_task)

    def disconnect(self) -> None:
        self.connected = False
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ConnectionError("Launcher disconnected"))
        for process in self.jobs.values():
            process.returncode = -1
            if not process.exited.done():
                process.exited.set_result(-1)


def _observe_task(task: asyncio.Task[Any]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logging.getLogger(__name__).error("Thin control task failed", exc_info=task.exception())


class PackageProxy(PackageInstallConsumer):
    def __init__(self, bridge: Bridge) -> None:
        self.bridge = bridge

    @property
    def _worker_id(self) -> str:
        return self.bridge.pool.worker_id

    async def _package_tool(self, operation: str, **payload: Any) -> str | None:
        result = await self.bridge.request(
            "package_tool", {"operation": operation, **payload}
        )
        error = result.get("error")
        return error if isinstance(error, str) else None

    async def _pip_install(self, package: str, version: str | None) -> str | None:
        return await self._package_tool("install", package=package, version=version)

    async def _pip_uninstall(self, package: str) -> str | None:
        return await self._package_tool("uninstall", package=package)

    async def _pip_install_requirements(self) -> str | None:
        return await self._package_tool("requirements")

    async def _recycle_workers(self) -> None:
        await self.bridge.pool.drain_and_restart_template()

    async def _update_pool_packages(self) -> None:
        pool = self.bridge.pool
        if not pool._started:
            return
        try:
            result = await self.bridge.request("package_tool", {"operation": "inventory"})
            packages = result["packages"]
            redis = await pool._get_redis()
            await redis.hset(
                f"bifrost:pool:{pool.worker_id}", "packages", json.dumps(packages)
            )
        except Exception as exc:
            logging.getLogger(__name__).warning("Failed to update packages in Redis: %s", exc)


class ApiExecutor(PoolWorker):
    def __init__(self, bridge: Bridge, api_admission: Admission) -> None:
        def ready() -> None:
            notification = asyncio.create_task(bridge.request("ready", {}))
            notification.add_done_callback(_observe_task)
        super().__init__(on_ready=ready)
        self.bridge = bridge
        self.api_admission = api_admission

    async def _initialize_runtime(self) -> bool:
        from src.services.execution.process_pool import _notify_requirements_failures
        from src.services.execution.requirements_setup_result import RequirementsInstallResult
        await _notify_requirements_failures(RequirementsInstallResult.from_json_dict(self.bridge.requirements))
        self.incarnation_id = await register_static_worker(hostname=self.bridge.hostname)
        return True

    async def _close_runtime(self) -> None:
        # The app lifespan owns this API replica's database engine.
        pass

    async def _start_background_control(self) -> list[asyncio.Task[Any]]:
        # src.main already starts this API replica's canonical controllers.
        return []

    def _make_workflow_consumer(self) -> Any:
        from src.dev.thin_remote_pool import ThinRemoteProcessPool
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        self.bridge.pool = ThinRemoteProcessPool(self.bridge, worker_id=str(self.incarnation_id))
        from src.services.execution.requirements_setup_result import RequirementsInstallResult
        self.bridge.pool._apply_requirements_status(
            RequirementsInstallResult.from_json_dict(self.bridge.requirements)
        )
        return WorkflowExecutionConsumer(pool=self.bridge.pool)

    def _make_package_consumer(self) -> PackageProxy:
        return PackageProxy(self.bridge)

    def _measure_budget(self) -> MemoryBudget:
        current, limit = self.bridge.memory()
        if current < 0 or limit <= 0:
            raise RuntimeError("Launcher telemetry unavailable")
        return MemoryBudget(limit_bytes=limit, baseline_bytes=current,
                            threshold=self.settings.memory_pressure_threshold)

    def _under_pressure(self) -> bool:
        current, limit = self.bridge.memory()
        return (current < 0 or limit <= 0
                or current >= limit * self.settings.memory_pressure_threshold
                or self.api_admission.pressured())

    async def _execute_platform_job(self, claim: Any) -> bool:
        return await run_claimed_platform_job(claim, backend=self.bridge)

    async def _claim_platform_job(self, fits_bytes: int) -> Any:
        return await claim_platform_job(backend="local", fits_bytes=fits_bytes,
                                        memory_sample=self.bridge.memory())

    async def _drain(self) -> None:
        if not self.bridge.connected:
            # Complete against the still-live owner fence, before run()
            # retires the worker registration and makes results stale.
            await self.bridge.pool._report_disconnected_routes()
        await super()._drain()

    async def _run_claim(self, claim: Any) -> None:
        if claim.handler == "workflow.execute":
            await super()._run_claim(claim)
            return
        # All logical launchers on this API process share this second budget.
        # Agent/summary allocations occur here, never in the launcher cgroup.
        key = str(claim.dispatch_id)
        try:
            while not self._lost:
                async with self.api_admission.claiming() as fits:
                    if fits is not None and claim.memory_request_bytes <= fits:
                        self.api_admission.hold(key, claim.memory_request_bytes)
                        break
                await asyncio.sleep(.1)
            if not self._lost:
                await super()._run_claim(claim)
        finally:
            self.api_admission.release(key)


_original_lifespan = app.router.lifespan_context
_bootstrap_token = ""
_sessions: set[asyncio.Task[Any]] = set()
_enrolled = False
_api_admission: Admission | None = None


@asynccontextmanager
async def lifespan(application: Any):
    global _bootstrap_token, _api_admission, _enrolled
    async with _original_lifespan(application):
        _enrolled = False
        _bootstrap_token = secrets.token_urlsafe(48)
        token_file = Path(os.environ["BIFROST_THIN_TOKEN_FILE"])
        token_file.write_text(_bootstrap_token)
        token_file.chmod(0o600)
        from src.config import get_settings
        threshold = get_settings().memory_pressure_threshold
        budget = measure_budget(threshold)
        _api_admission = Admission(budget_bytes=budget.budget_bytes,
                                   under_pressure=lambda: under_pressure(threshold),
                                   running_workflow_ids=set, workflow_growth=dict)
        try:
            yield
        finally:
            token_file.unlink(missing_ok=True)
            for task in list(_sessions):
                task.cancel()
            await asyncio.gather(*_sessions, return_exceptions=True)


async def thin_control(socket: WebSocket) -> None:
    global _enrolled
    supplied = socket.headers.get("authorization", "")
    if (_enrolled or not _bootstrap_token
            or not hmac.compare_digest(supplied, f"Bearer {_bootstrap_token}")):
        await socket.close(code=1008)
        return
    _enrolled = True
    await socket.accept()
    bridge: Bridge | None = None
    worker: ApiExecutor | None = None
    task: asyncio.Task[Any] | None = None
    try:
        async with asyncio.timeout(120):
            hello = await socket.receive_json()
        if hello.get("event") != "hello":
            await socket.close(code=1008)
            return
        bridge = Bridge(socket, hello)
        assert _api_admission is not None
        worker = ApiExecutor(bridge, _api_admission)
        async def execute() -> None:
            await worker.run()
        task = asyncio.create_task(execute(), name="thin-api-executor")
        _sessions.add(task)
        task.add_done_callback(_sessions.discard)
        task.add_done_callback(_observe_task)
        task.add_done_callback(lambda _: asyncio.create_task(socket.close()))
        while True:
            event = await socket.receive_json()
            if event.get("event") == "shutdown":
                worker.request_stop()
            else:
                await bridge.receive(event)
    except WebSocketDisconnect:
        pass
    finally:
        if worker is None:
            _enrolled = False
        if bridge is not None:
            bridge.disconnect()
            if bridge.results:
                await asyncio.gather(*bridge.results, return_exceptions=True)
        if worker is not None:
            worker._lost = True
            worker.request_stop()
        if task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(task), 20)


def create_host():
    """Explicit uvicorn factory; importing helpers never mutates the API app."""
    app.router.lifespan_context = lifespan
    app.add_api_websocket_route("/_dev/thin-control", thin_control)
    # The ordinary API mounts the MCP ASGI catch-all at '/'. This endpoint
    # belongs before that mount, like normal API routers.
    app.router.routes.insert(0, app.router.routes.pop())
    return app
