"""Run the platform's Node helper scripts (bundle.js, compile.js, tailwind.js)."""

from __future__ import annotations

import asyncio
import threading
import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path

from shared.app_build_errors import subprocess_memory_error


@dataclass(frozen=True)
class NodeScriptPolicy:
    """Typed resource budget for a particular Node helper."""

    name: str
    max_input_bytes: int
    max_output_bytes: int
    timeout_seconds: float
    admission_timeout_seconds: float
    max_old_space_mb: int
    concurrency: int


class NodeScriptLimitError(ValueError):
    """A Node helper exceeded an explicit platform resource budget."""


class NodeScriptInputLimitError(NodeScriptLimitError):
    """A Node helper's request input is too large."""


class NodeScriptAdmissionError(NodeScriptLimitError):
    """A Node helper could not obtain a bounded execution slot."""


class NodeScriptTimeoutError(NodeScriptLimitError):
    """A Node helper did not finish inside its allowed execution window."""


class NodeScriptOutputLimitError(NodeScriptLimitError):
    """A Node helper emitted too much stdout or stderr."""


class _OutputBudget:
    def __init__(self, limit: int, policy: NodeScriptPolicy) -> None:
        self._limit = limit
        self._policy = policy
        self._total = 0
        self._lock = asyncio.Lock()

    async def consume(self, size: int, stream_name: str) -> None:
        async with self._lock:
            self._total += size
            if self._total > self._limit:
                raise NodeScriptOutputLimitError(
                    f"{self._policy.name} exceeded its {self._limit}-byte combined "
                    f"stdout/stderr output limit while reading {stream_name}."
                )


DEFAULT_NODE_SCRIPT_POLICY = NodeScriptPolicy(
    name="node helper",
    max_input_bytes=32 * 1024 * 1024,
    max_output_bytes=64 * 1024 * 1024,
    timeout_seconds=60,
    admission_timeout_seconds=10,
    max_old_space_mb=512,
    concurrency=2,
)
APP_COMPILER_NODE_POLICY = NodeScriptPolicy(
    name="App compiler",
    # JSON escaping adds overhead to the 32 MiB source-tree budget.
    max_input_bytes=48 * 1024 * 1024,
    max_output_bytes=64 * 1024 * 1024,
    timeout_seconds=60,
    admission_timeout_seconds=10,
    max_old_space_mb=512,
    concurrency=2,
)
TAILWIND_NODE_POLICY = NodeScriptPolicy(
    name="App Tailwind compiler",
    max_input_bytes=32 * 1024 * 1024,
    max_output_bytes=16 * 1024 * 1024,
    timeout_seconds=60,
    admission_timeout_seconds=10,
    max_old_space_mb=512,
    concurrency=2,
)
APP_BUNDLER_NODE_POLICY = NodeScriptPolicy(
    name="App bundler",
    max_input_bytes=1 * 1024 * 1024,
    max_output_bytes=1 * 1024 * 1024,
    timeout_seconds=90,
    admission_timeout_seconds=15,
    max_old_space_mb=768,
    concurrency=1,
)

APP_NODE_TOTAL_CONCURRENCY = 2


@dataclass
class _LoopAdmissions:
    per_policy: dict[NodeScriptPolicy, weakref.ReferenceType[asyncio.Semaphore]] = field(
        default_factory=dict
    )


_admission_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopAdmissions] = (
    weakref.WeakKeyDictionary()
)
_process_admission = threading.BoundedSemaphore(APP_NODE_TOTAL_CONCURRENCY)


def _admission_locks_for(
    policy: NodeScriptPolicy,
) -> asyncio.Semaphore:
    admissions = _admission_locks.setdefault(asyncio.get_running_loop(), _LoopAdmissions())
    lock = admissions.per_policy.get(policy)
    policy_lock = lock() if lock is not None else None
    if policy_lock is None:
        policy_lock = asyncio.Semaphore(policy.concurrency)
        admissions.per_policy[policy] = weakref.ref(policy_lock)
    return policy_lock


async def _acquire_process_slot(timeout_seconds: float) -> None:
    """Acquire the process-wide Node cap without binding a semaphore to a loop."""
    deadline = time.monotonic() + timeout_seconds
    while not _process_admission.acquire(blocking=False):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        await asyncio.sleep(min(0.05, remaining))


async def _read_limited(
    stream: asyncio.StreamReader,
    *,
    stream_name: str,
    budget: _OutputBudget,
) -> bytes:
    chunks: list[bytes] = []
    while chunk := await stream.read(64 * 1024):
        await budget.consume(len(chunk), stream_name)
        chunks.append(chunk)
    return b"".join(chunks)


async def _write_stdin(proc: asyncio.subprocess.Process, input_data: bytes) -> None:
    assert proc.stdin is not None
    try:
        proc.stdin.write(input_data)
        await proc.stdin.drain()
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
        # Node can exit on a startup or heap failure before consuming stdin.
        # Its output/exit status remains the useful diagnostic.
        return
    finally:
        proc.stdin.close()
        try:
            await proc.stdin.wait_closed()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Closing stdin can repeat the child's early-exit pipe error;
            # stdout, stderr and its exit status carry the actual failure.
            pass


async def _terminate_and_reap(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        proc.kill()
    await proc.wait()


async def run_node_script(
    script: Path,
    input_data: bytes,
    *,
    policy: NodeScriptPolicy = DEFAULT_NODE_SCRIPT_POLICY,
) -> tuple[int | None, bytes, bytes]:
    """Run ``node <script>`` with ``input_data`` on stdin; return (returncode, stdout, stderr).

    If the caller is cancelled (request disconnect, ``asyncio.wait_for``
    timeout, shutdown) the child is killed and reaped instead of being left
    running after the awaiting coroutine is gone.
    """
    if len(input_data) > policy.max_input_bytes:
        raise NodeScriptInputLimitError(
            f"{policy.name} input is {len(input_data)} bytes; the maximum is "
            f"{policy.max_input_bytes} bytes."
        )
    policy_admission = _admission_locks_for(policy)
    policy_acquired = False
    try:
        await asyncio.wait_for(
            policy_admission.acquire(), timeout=policy.admission_timeout_seconds
        )
        policy_acquired = True
        await _acquire_process_slot(policy.admission_timeout_seconds)
    except TimeoutError as exc:
        if policy_acquired:
            policy_admission.release()
        raise NodeScriptAdmissionError(
            f"{policy.name} is busy; retry after other App builds finish."
        ) from exc
    except BaseException:
        if policy_acquired:
            policy_admission.release()
        raise
    proc: asyncio.subprocess.Process | None = None
    tasks: list[asyncio.Task] = []
    try:
        proc = await asyncio.create_subprocess_exec(
            "node", f"--max-old-space-size={policy.max_old_space_mb}", str(script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdout is not None
        assert proc.stderr is not None
        output_budget = _OutputBudget(policy.max_output_bytes, policy)
        tasks = [
            asyncio.create_task(_write_stdin(proc, input_data)),
            asyncio.create_task(_read_limited(
                proc.stdout, stream_name="stdout",
                budget=output_budget,
            )),
            asyncio.create_task(_read_limited(
                proc.stderr, stream_name="stderr",
                budget=output_budget,
            )),
            asyncio.create_task(proc.wait()),
        ]
        try:
            _, stdout, stderr, _ = await asyncio.wait_for(
                asyncio.gather(*tasks), timeout=policy.timeout_seconds
            )
        except TimeoutError as exc:
            raise NodeScriptTimeoutError(
                f"{policy.name} exceeded its {policy.timeout_seconds}-second timeout."
            ) from exc
    finally:
        if proc is not None and proc.returncode is None:
            await _terminate_and_reap(proc)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        _process_admission.release()
        policy_admission.release()
    memory_error = subprocess_memory_error(proc.returncode, stderr, stdout)
    if memory_error is not None:
        stderr = str(memory_error).encode()
    return proc.returncode, stdout, stderr
