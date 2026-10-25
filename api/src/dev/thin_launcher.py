"""Footprint experiment: OS supervision without application imports.

Only the API can issue commands. Durable claims, SDK authorization and result
commits stay in the ordinary consumers hosted by ``thin_host``.
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
from typing import Any

from src.services.execution.cpu_sampler import CPUSampler, get_clock_ticks
from src.services.execution.memory_monitor import get_cgroup_memory
from src.services.execution.template_process import TemplateProcess
from src.worker.health import clear_ready, mark_ready

logger = logging.getLogger(__name__)


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


class Launcher:
    def __init__(self, socket: Any) -> None:
        self.socket = socket
        self.template = TemplateProcess()
        self.children: dict[str, Child] = {}
        self.jobs: dict[str, asyncio.subprocess.Process] = {}
        self._template_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self.pressure_threshold: float | None = None
        self.dangerous_memory_ratio: float | None = None

    async def send(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.socket.send(json.dumps(payload, default=str, separators=(",", ":")))

    def task(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self.task_finished)

    def task_finished(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Launcher supervision failed", exc_info=task.exception())
            asyncio.create_task(self.socket.close(code=1011, reason="Launcher supervision failed"))

    async def start(self) -> None:
        # This existing helper owns requirements IO and exits before ready.
        result = await self.helper("src.services.execution.requirements_setup_helper")
        await asyncio.to_thread(self.template.start)
        current, limit = get_cgroup_memory()
        if current < 0 or limit <= 0:
            raise RuntimeError("Thin spike requires a readable, bounded cgroup v2")
        await self.send({"event": "hello", "current": current, "limit": limit,
                         "requirements": result, "hostname": os.environ["HOSTNAME"]})
        self.task(self.monitor())

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

    async def command(self, request: dict[str, Any]) -> None:
        action, payload = request["action"], request["payload"]
        try:
            response: dict[str, Any] = {}
            if action in ("workflow", "service"):
                current, limit = get_cgroup_memory()
                if self.pressure_threshold is None:
                    raise RuntimeError("Launcher resource policy has not been configured")
                if current < 0 or limit <= 0 or current >= limit * self.pressure_threshold:
                    raise MemoryError("Launcher memory pressure rejects fork")
                route_id = payload["route_id"]
                if route_id in self.children or route_id in self.jobs:
                    raise ValueError("Duplicate route")
                async with self._template_lock:
                    pid, work, result = await asyncio.to_thread(
                        self.template.fork, worker_id=route_id, sdk_socket_path=None,
                    )
                context = payload["context"]
                timeout = context.get("timeout_seconds", 300) if action == "workflow" else 0
                child = Child(route_id, payload["execution_id"], pid, work, result,
                              time.monotonic(), timeout,
                              service=payload.get("service"),
                              memory_kill_growth_bytes=payload.get("memory_kill_growth_bytes", 0),
                              grace=payload.get("graceful_shutdown_seconds", 5))
                child.sampler = CPUSampler(pid=pid, clock_ticks=get_clock_ticks())
                child.sampler.set_baseline(time.monotonic())
                child.rss_start = child.sampler.start_process_rss_bytes or 0
                self.children[route_id] = child
                asyncio.get_running_loop().add_reader(result.fileno(), self.result_ready, route_id)
                work.put_nowait((child.execution_id, context))
                response["pid"] = pid
            elif action == "platform_job":
                route_id = payload["route_id"]
                if route_id in self.jobs or route_id in self.children:
                    raise ValueError("Duplicate route")
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "src.jobs.platform.runner",
                    payload["job_id"], payload["lease_token"], start_new_session=True,
                )
                self.jobs[route_id] = process
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
                response = await self.helper("src.dev.thin_package_helper", json.dumps(payload))
            elif action == "configure":
                pressure = float(payload["pressure_threshold"])
                dangerous = float(payload["dangerous_memory_ratio"])
                if not 0 < pressure < dangerous < 1:
                    raise ValueError("Invalid launcher memory policy")
                self.pressure_threshold, self.dangerous_memory_ratio = pressure, dangerous
            elif action == "ready":
                mark_ready()
            else:
                raise ValueError("Unknown launcher command")
            await self.send({"reply": request["id"], "result": response})
        except Exception as exc:
            await self.send({"reply": request["id"], "error_type": type(exc).__name__,
                             "error": str(exc)})

    async def wait_job(self, route_id: str, process: asyncio.subprocess.Process) -> None:
        code = await process.wait()
        try:
            await self.send({"event": "job_exit", "route_id": route_id, "returncode": code})
        finally:
            self.jobs.pop(route_id, None)

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
        await self.send({"event": "result", "route_id": child.route_id, "result": result})

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
            if not self.template.is_alive():
                raise RuntimeError("Execution template died")
            current, limit = get_cgroup_memory()
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
            if current < 0 or limit <= 0:
                raise RuntimeError("Launcher cgroup telemetry became unavailable")
            self.stop_largest_when_dangerous(current, limit)
            if tick % 4 == 0:
                async with self._template_lock:
                    await asyncio.to_thread(self.template.collect_child_exit_statuses)
                await self.send({"event": "sample", "current": current, "limit": limit,
                                 "runs": samples})
            tick += 1
            await asyncio.sleep(.25)

    def stop_largest_when_dangerous(self, current: int, limit: int) -> None:
        """Mirror the canonical pool: stop one largest-growing child per tick."""
        if self.dangerous_memory_ratio is None or current < limit * self.dangerous_memory_ratio:
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
        clear_ready()
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


async def main() -> None:
    from websockets.asyncio.client import connect

    clear_ready()
    token_file = Path(os.environ["BIFROST_THIN_TOKEN_FILE"])
    while not token_file.exists():
        await asyncio.sleep(.1)
    token = token_file.read_text().strip()
    token_file.unlink()
    async with connect(os.environ["BIFROST_THIN_CONTROL_URL"],
                       additional_headers={"Authorization": f"Bearer {token}"},
                       max_size=32 * 2**20, open_timeout=10) as socket:
        launcher = Launcher(socket)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: launcher.task(launcher.send({"event": "shutdown"})))
        try:
            await launcher.start()
            async for raw in socket:
                launcher.task(launcher.command(json.loads(raw)))
        finally:
            await launcher.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
