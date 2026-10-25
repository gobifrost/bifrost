"""Lightweight physical runner: OS supervision without application imports.

Only the API can issue commands. Durable claims, SDK authorization and result
commits stay in the ordinary consumers hosted by ``remote.host``.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from src.services.execution.cpu_sampler import CPUSampler, get_clock_ticks
from src.services.execution.memory_monitor import effective_memory_sample, memory_pressure_ratio
from src.services.execution.template_process import TemplateProcess
from src.worker.remote.identity import LauncherIdentity, load_launcher_identity
from src.worker.remote.protocol import (
    ControlEnvelope,
    FragmentAssembler,
    Hello,
    fragment,
    new_nonce,
    sign_hello,
)

logger = logging.getLogger(__name__)
LAUNCHER_DRAIN_GRACE_SECONDS = 300.0


async def _next_control_frame(
    socket: Any,
    stopping: asyncio.Event,
    retired: asyncio.Event | None = None,
    drain_task: asyncio.Task[None] | None = None,
) -> str | None:
    """Receive one frame while still observing process shutdown signals."""
    receive = asyncio.create_task(socket.recv())
    wake_tasks = {asyncio.create_task(stopping.wait())}
    if retired is not None:
        wake_tasks.add(asyncio.create_task(retired.wait()))
    waiters = {receive, *wake_tasks}
    if drain_task is not None:
        waiters.add(drain_task)
    done, pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        if task is not drain_task:
            task.cancel()
    await asyncio.gather(*(task for task in pending if task is not drain_task), return_exceptions=True)
    if receive not in done:
        return None
    return receive.result()


async def _receive_control_commands(
    launcher: Launcher,
    socket: Any,
    stopping: asyncio.Event,
    drain_task: asyncio.Task[None] | None = None,
) -> asyncio.Task[None] | None:
    """Continue receiving ACKs and retirement while physical drain runs."""
    assembler = FragmentAssembler()
    while not launcher.retired.is_set():
        raw = await _next_control_frame(socket, stopping, launcher.retired, drain_task)
        if raw is None:
            if launcher.retired.is_set() or (drain_task is not None and drain_task.done()):
                return drain_task
            if drain_task is None:
                drain_task = asyncio.create_task(launcher.begin_drain())
            continue
        frame = ControlEnvelope.from_json(raw)
        if frame.session_generation != launcher.session_generation:
            raise RuntimeError("stale API control session")
        if frame.sequence <= launcher._last_command_sequence:
            raise RuntimeError("stale API control command")
        launcher._last_command_sequence = frame.sequence
        command = assembler.feed(frame)
        if command is None:
            continue
        if command.kind == "ack":
            event_id = command.payload.get("event_id")
            if isinstance(event_id, str):
                launcher.pending_terminal.pop(event_id, None)
                launcher.pending_terminal_inventory.pop(event_id, None)
            continue
        if command.kind != "command":
            raise RuntimeError("unexpected API control message")
        launcher.task(launcher.command(command.payload, generation=launcher.session_generation))
    return drain_task


async def _shutdown_launcher(
    launcher: Launcher,
    drain_task: asyncio.Task[None] | None,
    stopping: asyncio.Event,
) -> None:
    """Honor one bounded physical drain before stopping local children."""
    if stopping.is_set() and drain_task is None:
        drain_task = asyncio.create_task(launcher.begin_drain())
    if drain_task is not None:
        await asyncio.gather(drain_task, return_exceptions=True)
    await launcher.stop()


@dataclass
class Child:
    route_id: str
    execution_id: str
    pid: int
    work: Any
    result: Any
    started: float
    timeout: float
    service: dict[str, Any] | None = None
    memory_kill_growth_bytes: int = 0
    grace: float = 5
    rss_start: int = 0
    rss_peak: int = 0
    growth_peak: int = 0
    current_growth: int = 0
    stopping: bool = False
    sampler: CPUSampler | None = None
    terminal_reason: tuple[str, str] | None = None
    active_execution: dict[str, Any] | None = None
    started_at: str | None = None
    memory_request_bytes: int | None = None


class Launcher:
    def __init__(
        self,
        socket: Any,
        *,
        identity: LauncherIdentity,
        secret: str,
        on_ready: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
    ) -> None:
        self.socket = socket
        self.identity = identity
        self.secret = secret
        self.session_generation = 0
        self._sequence = 0
        self._last_command_sequence = 0
        self._hello_sequence = 0
        self.on_ready = on_ready or (lambda: None)
        self.on_stop = on_stop or (lambda: None)
        self.requirements: dict[str, Any] = {}
        self.pending_terminal: dict[str, dict[str, Any]] = {}
        self.pending_terminal_inventory: dict[str, dict[str, Any]] = {}
        self.template = TemplateProcess()
        self.children: dict[str, Child] = {}
        self.jobs: dict[str, asyncio.subprocess.Process] = {}
        self.job_claims: dict[str, dict[str, str | int]] = {}
        self._template_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._monitor_task: asyncio.Task[Any] | None = None
        self.failed = False
        self.pressure_threshold: float | None = None
        self.dangerous_memory_ratio: float | None = None
        self.baseline_bytes: int | None = None
        self.retired = asyncio.Event()

    async def send(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            if self.session_generation < 1:
                raise ConnectionError("launcher has no fenced API session")
            envelope = ControlEnvelope(
                session_generation=self.session_generation,
                sequence=self._sequence + 1,
                kind="event",
                payload=payload,
            )
            sent = 0
            for frame in fragment(envelope):
                try:
                    await self.socket.send(frame.to_json())
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    raise ConnectionError("remote control transport is unavailable") from exc
                sent += 1
            self._sequence += sent

    async def send_terminal(
        self, payload: dict[str, Any], *, inventory: dict[str, Any]
    ) -> None:
        """Keep terminal results until the current fenced API owner ACKs them."""
        event_id = str(uuid4())
        event = {**payload, "_event_id": event_id}
        self.pending_terminal[event_id] = event
        self.pending_terminal_inventory[event_id] = {"event_id": event_id, **inventory}
        await self.send(event)

    def task(self, coroutine: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self.task_finished)
        return task

    def task_finished(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            if task is self._monitor_task:
                self.failed = True
                self.on_stop()
            logger.error("Launcher supervision failed", exc_info=task.exception())
            asyncio.create_task(self.socket.close(code=1011, reason="Launcher supervision failed"))

    async def start(self) -> None:
        # This existing helper owns requirements IO and exits before ready.
        result = await self.helper("src.services.execution.requirements_setup_helper")
        self.requirements = result
        await asyncio.to_thread(self.template.start)
        current, limit = effective_memory_sample()
        self.baseline_bytes = current
        self._monitor_task = self.task(self.monitor())

    async def hello(self, requirements: dict[str, Any]) -> None:
        """Authenticate each new WebSocket; the physical process stays alive."""
        current, limit = effective_memory_sample()
        self._hello_sequence += 1
        hello = Hello(
            launcher_id=self.identity.launcher_id,
            incarnation_id=self.identity.incarnation_id,
            job_uid=self.identity.job_uid,
            pod_uid=self.identity.pod_uid,
            hostname=self.identity.hostname,
            form=self.identity.form,
            issued_at=int(time.time()),
            nonce=new_nonce(),
            hello_sequence=self._hello_sequence,
        )
        await self.socket.send(json.dumps({
            "event": "hello",
            "hello": sign_hello(self.secret, hello).to_dict(),
            "sample": {
                "current": current, "limit": limit,
                "pressure_ratio": memory_pressure_ratio(),
            },
            "requirements": requirements,
        }, separators=(",", ":")))
        welcome = json.loads(await self.socket.recv())
        if welcome.get("event") != "welcome" or not isinstance(welcome.get("session_generation"), int):
            raise RuntimeError("remote host refused launcher session")
        self.session_generation = welcome["session_generation"]
        self._sequence = 0
        self._last_command_sequence = 0

    def inventory(self) -> dict[str, Any]:
        """Describe live physical routes for fenced API-owner adoption.

        Context and authoritative claim state are intentionally not copied over
        the wire: the adopting API reconstructs them from canonical DB claims.
        """
        routes: list[dict[str, Any]] = []
        for child in self.children.values():
            routes.append({
                "route_id": child.route_id,
                "kind": "service" if child.service is not None else "workflow",
                "subject_id": child.execution_id,
                "pid": child.pid,
                "service": child.service,
                "active_execution": child.active_execution,
                "started_at": child.started_at,
                "timeout_seconds": child.timeout,
                "memory_request_bytes": child.memory_request_bytes,
            })
        for route_id, process in self.jobs.items():
            claim = self.job_claims.get(route_id, {})
            routes.append({
                "route_id": route_id,
                "kind": "platform_job",
                "subject_id": claim.get("job_id"),
                "pid": process.pid,
                "lease_token": claim.get("lease_token"),
                "memory_request_bytes": claim.get("memory_request_bytes"),
            })
        return {
            "version": 1,
            "baseline_bytes": self.baseline_bytes,
            "routes": routes,
            "terminal_routes": list(self.pending_terminal_inventory.values()),
        }

    async def helper(self, module: str, *args: str) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", module, *args,
            stdout=asyncio.subprocess.PIPE, start_new_session=True,
        )
        try:
            stdout, _ = await process.communicate()
        except BaseException:
            await self.kill_process(process)
            raise
        if process.returncode:
            raise RuntimeError(f"{module} failed with exit code {process.returncode}")
        return json.loads(stdout)

    async def command(self, request: dict[str, Any], *, generation: int) -> None:
        """Execute one command only while its fenced session remains current."""
        if generation != self.session_generation:
            return
        action, payload = request["action"], request["payload"]
        try:
            response: dict[str, Any] = {}
            if action in ("workflow", "service"):
                current, limit = effective_memory_sample()
                if self.pressure_threshold is None:
                    raise RuntimeError("Launcher resource policy has not been configured")
                if (
                    current < 0
                    or limit <= 0
                    or memory_pressure_ratio() >= self.pressure_threshold
                ):
                    raise MemoryError("Launcher memory pressure rejects fork")
                route_id = payload["route_id"]
                if route_id in self.children or route_id in self.jobs:
                    raise ValueError("Duplicate route")
                if generation != self.session_generation:
                    return
                async with self._template_lock:
                    if generation != self.session_generation:
                        return
                    pid, work, result = await asyncio.to_thread(
                        self.template.fork, worker_id=route_id, sdk_socket_path=None,
                    )
                context = payload["context"]
                timeout = context.get("timeout_seconds", 300) if action == "workflow" else 0
                child = Child(route_id, payload["execution_id"], pid, work, result,
                              time.monotonic(), timeout,
                              service=payload.get("service"),
                              memory_kill_growth_bytes=payload.get("memory_kill_growth_bytes", 0),
                              grace=payload.get("graceful_shutdown_seconds", 5),
                              active_execution=payload.get("active_execution"),
                              started_at=payload.get("started_at"),
                              memory_request_bytes=payload.get("memory_request_bytes"))
                child.sampler = CPUSampler(pid=pid, clock_ticks=get_clock_ticks())
                child.sampler.set_baseline(time.monotonic())
                child.rss_start = child.sampler.start_process_rss_bytes or 0
                self.children[route_id] = child
                asyncio.get_running_loop().add_reader(result.fileno(), self.result_ready, route_id)
                work.put_nowait((child.execution_id, context))
                response["pid"] = pid
            elif action == "platform_job":
                if self.pressure_threshold is None:
                    raise RuntimeError("Launcher resource policy has not been configured")
                if memory_pressure_ratio() >= self.pressure_threshold:
                    raise MemoryError("Launcher memory pressure rejects platform job")
                route_id = payload["route_id"]
                if route_id in self.jobs or route_id in self.children:
                    raise ValueError("Duplicate route")
                if generation != self.session_generation:
                    return
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "src.jobs.platform.runner",
                    payload["job_id"], payload["lease_token"], start_new_session=True,
                )
                self.jobs[route_id] = process
                self.job_claims[route_id] = {
                    "job_id": str(payload["job_id"]),
                    "lease_token": str(payload["lease_token"]),
                    "memory_request_bytes": int(payload["memory_request_bytes"]),
                }
                self.task(self.wait_job(route_id, process))
            elif action == "terminate_job":
                process = self.jobs.get(payload["route_id"])
                if process is not None:
                    await self.kill_process(process)
            elif action == "terminate":
                await self.terminate(payload["route_id"], payload.get("error_type", "CancelledError"),
                                     payload.get("error", "Cancelled"), payload.get("grace_seconds"))
            elif action == "recycle":
                while self.children:
                    await asyncio.sleep(.05)
                async with self._template_lock:
                    await asyncio.to_thread(self.template.shutdown)
                    await asyncio.to_thread(self.template.start)
            elif action == "package_tool":
                response = await self.helper("src.worker.remote.package_helper", json.dumps(payload))
            elif action == "configure":
                pressure = float(payload["pressure_threshold"])
                dangerous = float(payload["dangerous_memory_ratio"])
                if not 0 < pressure < dangerous < 1:
                    raise ValueError("Invalid launcher memory policy")
                self.pressure_threshold, self.dangerous_memory_ratio = pressure, dangerous
            elif action == "ready":
                self.on_ready()
            elif action == "retired":
                self.retired.set()
            else:
                raise ValueError("Unknown launcher command")
            if generation == self.session_generation:
                await self.send({"reply": request["id"], "result": response})
        except Exception as exc:
            if generation == self.session_generation:
                await self.send({"reply": request["id"], "error_type": type(exc).__name__,
                                 "error": str(exc)})

    async def wait_job(self, route_id: str, process: asyncio.subprocess.Process) -> None:
        code = await process.wait()
        try:
            await self.send_terminal(
                {"event": "job_exit", "route_id": route_id, "returncode": code},
                inventory={
                    "route_id": route_id,
                    "kind": "platform_job",
                    "subject_id": self.job_claims.get(route_id, {}).get("job_id"),
                    "lease_token": self.job_claims.get(route_id, {}).get("lease_token"),
                    "memory_request_bytes": self.job_claims.get(route_id, {}).get("memory_request_bytes"),
                },
            )
        finally:
            self.jobs.pop(route_id, None)
            self.job_claims.pop(route_id, None)

    @staticmethod
    async def kill_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()

    def result_ready(self, route_id: str) -> None:
        child = self.children.get(route_id)
        if child is None:
            return
        asyncio.get_running_loop().remove_reader(child.result.fileno())
        try:
            result = child.result.get_nowait()
        except (EOFError, OSError):
            result = self.failure(child, "ProcessCrashError", "Child exited without a result")
        self.task(self.finish(child, result))

    @staticmethod
    def failure(child: Child, error_type: str, error: str) -> dict[str, Any]:
        return {"execution_id": child.execution_id, "success": False, "status": "Failed",
                "error": error, "error_message": error, "error_type": error_type,
                "duration_ms": int((time.monotonic() - child.started) * 1000)}

    async def finish(self, child: Child, result: dict[str, Any]) -> None:
        if self.children.pop(child.route_id, None) is None:
            return
        if child.terminal_reason is not None:
            result = self.failure(child, *child.terminal_reason)
        if child.sampler is not None:
            child.sampler.sample(time.monotonic())
            child.rss_peak = max(child.rss_peak, child.sampler.peak_process_rss_bytes or 0)
            child.growth_peak = max(child.growth_peak, child.rss_peak - child.rss_start)
        asyncio.get_running_loop().remove_reader(child.result.fileno())
        child.work.close()
        child.result.close()
        metrics = result.setdefault("metrics", {})
        if isinstance(metrics, dict):
            metrics["peak_memory_growth_bytes"] = max(metrics.get("peak_memory_growth_bytes") or 0,
                                                       child.growth_peak)
            if child.terminal_reason is not None and child.terminal_reason[0] == "MemoryLimitExceeded":
                metrics["peak_memory_growth_bytes"] = max(
                    metrics["peak_memory_growth_bytes"], child.memory_kill_growth_bytes or 0,
                )
            metrics["peak_process_rss_bytes"] = max(metrics.get("peak_process_rss_bytes") or 0,
                                                     child.rss_peak)
            if child.sampler is not None and child.sampler.peak_cpu_cores is not None:
                metrics["peak_cpu_cores"] = child.sampler.peak_cpu_cores
        await self.send_terminal(
            {"event": "result", "route_id": child.route_id, "result": result},
            inventory={
                "route_id": child.route_id,
                "kind": "service" if child.service is not None else "workflow",
                "subject_id": child.execution_id,
                "service": child.service,
                "active_execution": child.active_execution,
                "started_at": child.started_at,
                "timeout_seconds": child.timeout,
                "memory_request_bytes": child.memory_request_bytes,
            },
        )

    async def terminate(self, route_id: str, error_type: str, error: str,
                        grace: float | None = None) -> None:
        child = self.children.get(route_id)
        if child is None or child.stopping:
            return
        child.stopping = True
        if child.service is None or error_type == "MemoryLimitExceeded":
            child.terminal_reason = (error_type, error)
        with contextlib.suppress(ProcessLookupError):
            os.kill(child.pid, signal.SIGTERM)
        deadline = time.monotonic() + (child.grace if grace is None else grace)
        while route_id in self.children and time.monotonic() < deadline:
            if not Path(f"/proc/{child.pid}").exists():
                break
            await asyncio.sleep(.05)
        if route_id in self.children:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child.pid, signal.SIGKILL)
            await self.finish(child, self.failure(child, error_type, error))

    async def monitor(self) -> None:
        tick = 0
        while True:
            # A package recycle intentionally stops the template while holding
            # this lock. Observe liveness only outside that replacement window.
            async with self._template_lock:
                if not self.template.is_alive():
                    # Preserve the canonical pool's template-crash recovery;
                    # live children retain their existing pipes and identities.
                    logger.warning("Execution template died; replacing it")
                    await asyncio.to_thread(self.template.start)
            current, limit = effective_memory_sample()
            samples = {}
            for child in list(self.children.values()):
                assert child.sampler is not None
                child.sampler.sample(time.monotonic())
                rss = child.sampler.last_process_rss_bytes or 0
                child.rss_peak = max(child.rss_peak, rss)
                child.growth_peak = max(child.growth_peak, rss - child.rss_start)
                child.current_growth = max(0, rss - child.rss_start)
                samples[child.route_id] = {"growth_bytes": child.current_growth,
                                          "memory_peak_bytes": child.growth_peak,
                                          "peak_cpu_cores": child.sampler.peak_cpu_cores,
                                          "rss_bytes": rss}
                if child.stopping:
                    continue
                reason = None
                if child.timeout and time.monotonic() - child.started > child.timeout:
                    reason = ("TimeoutError", "Execution timed out")
                if reason:
                    # Once observed, a deadline/resource violation owns the
                    # terminal outcome even if the result pipe becomes ready.
                    if child.service is None:
                        child.terminal_reason = reason
                    self.task(self.terminate(child.route_id, *reason, grace=0))
            self.stop_largest_when_dangerous(
                current, limit,
                dangerous=memory_pressure_ratio() >= (self.dangerous_memory_ratio or 1),
            )
            if tick % 4 == 0:
                async with self._template_lock:
                    await asyncio.to_thread(self.template.collect_child_exit_statuses)
                with contextlib.suppress(ConnectionError):
                    await self.send({"event": "sample", "current": current, "limit": limit,
                                     "pressure_ratio": memory_pressure_ratio(), "runs": samples})
            tick += 1
            await asyncio.sleep(.25)

    def stop_largest_when_dangerous(
        self, current: int, limit: int, *, dangerous: bool | None = None
    ) -> None:
        """Mirror the canonical pool: stop one largest-growing child per tick."""
        if self.dangerous_memory_ratio is None or dangerous is False or (
            dangerous is None and current < limit * self.dangerous_memory_ratio
        ):
            return
        candidates = [child for child in self.children.values()
                      if not child.stopping and child.terminal_reason is None]
        if not candidates:
            return
        child = max(candidates, key=lambda item: item.current_growth)
        reason = ("MemoryLimitExceeded", "Runner memory pressure")
        child.terminal_reason = reason
        self.task(self.terminate(child.route_id, *reason, grace=0))

    async def stop(self) -> None:
        self.on_stop()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for child in self.children.values():
            with contextlib.suppress(ProcessLookupError):
                os.kill(child.pid, signal.SIGKILL)
            child.work.close()
            child.result.close()
        self.children.clear()
        await asyncio.gather(*(self.kill_process(p) for p in self.jobs.values()))
        await asyncio.to_thread(self.template.shutdown)

    async def begin_drain(self) -> None:
        """Ask the API to drain and retain physical children for its grace."""
        with contextlib.suppress(ConnectionError):
            await self.send({"event": "draining"})
        deadline = time.monotonic() + LAUNCHER_DRAIN_GRACE_SECONDS
        # A child leaves ``children`` before its terminal result is ACKed.
        # Do not cancel that replay task merely because the process itself
        # exited; the canonical owner must persist the terminal outcome.
        while (self.children or self.jobs or self.pending_terminal) and time.monotonic() < deadline:
            await asyncio.sleep(0.1)


async def run_launcher(
    *,
    on_ready: Callable[[], None] | None = None,
    on_stop: Callable[[], None] | None = None,
) -> int:
    """Run one physical launcher, reconnecting without killing live children."""
    from websockets.asyncio.client import connect

    api_url = os.environ.get("BIFROST_API_URL", "http://api:8000").rstrip("/")
    control_url = api_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    control_url += "/api/worker-control"
    identity = load_launcher_identity()
    secret = os.environ["BIFROST_SECRET_KEY"]
    launcher: Launcher | None = None
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    try:
        drain_task: asyncio.Task[None] | None = None
        while launcher is None or (not launcher.retired.is_set() and not launcher.failed):
            try:
                async with connect(control_url, max_size=2**20, open_timeout=10) as socket:
                    if launcher is None:
                        candidate = Launcher(
                            socket, identity=identity, secret=secret,
                            on_ready=on_ready, on_stop=on_stop,
                        )
                        # Enroll before the potentially minute-long requirements
                        # install. The API's ordinary worker heartbeat can then
                        # keep the canonical row alive while it waits for the
                        # inventory below; no work is ready to route yet.
                        started = False
                        try:
                            await candidate.hello({})
                            setup = asyncio.create_task(candidate.start())
                            while not setup.done():
                                try:
                                    await asyncio.wait_for(asyncio.shield(setup), timeout=5)
                                except TimeoutError:
                                    current, limit = effective_memory_sample()
                                    with contextlib.suppress(ConnectionError):
                                        await candidate.send({
                                            "event": "sample", "current": current,
                                            "limit": limit,
                                            "pressure_ratio": memory_pressure_ratio(), "runs": {},
                                        })
                            await asyncio.gather(setup)
                            started = True
                        finally:
                            if not started:
                                await candidate.stop()
                        launcher = candidate
                    else:
                        launcher.socket = socket
                        await launcher.hello(launcher.requirements)
                    await launcher.send({
                        "event": "inventory", "inventory": launcher.inventory(),
                        "requirements": launcher.requirements,
                    })
                    for event in launcher.pending_terminal.values():
                        await launcher.send(event)
                    drain_task = await _receive_control_commands(
                        launcher, socket, stopping, drain_task
                    )
                    if stopping.is_set() and drain_task is not None and drain_task.done():
                        break
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Remote control connection lost; preserving local children", exc_info=True)
                if stopping.is_set() or (launcher is not None and launcher.failed):
                    break
                await asyncio.sleep(1)
    finally:
        if launcher is not None:
            await _shutdown_launcher(launcher, drain_task, stopping)
    return 1 if launcher is not None and launcher.failed else 0


async def main() -> int:
    return await run_launcher()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(main()))
