"""run_node_script never leaves a node child running after its caller is gone."""

import asyncio
import os
import threading

import pytest

from src.services import node_subprocess
from src.services.node_subprocess import (
    NodeScriptAdmissionError,
    NodeScriptOutputLimitError,
    NodeScriptPolicy,
    NodeScriptTimeoutError,
    run_node_script,
)
from shared.app_build_errors import app_build_memory_message


@pytest.mark.asyncio
async def test_returns_exit_code_and_output(tmp_path):
    script = tmp_path / "echo.js"
    script.write_text(
        "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{process.stdout.write(s.toUpperCase());process.exit(3)});"
    )
    assert await run_node_script(script, b"hi") == (3, b"HI", b"")


@pytest.mark.asyncio
async def test_node_oom_output_includes_recovery_without_allocating_memory(tmp_path):
    script = tmp_path / "oom.js"
    script.write_text(
        "process.stderr.write('FATAL ERROR: JavaScript heap out of memory');process.exit(134);"
    )
    code, stdout, stderr = await run_node_script(script, b"")
    assert code == 134
    assert stdout == b""
    assert stderr.decode() == app_build_memory_message()


@pytest.mark.asyncio
async def test_cancelled_caller_kills_and_reaps_the_child(tmp_path, monkeypatch):
    script = tmp_path / "hang.js"
    script.write_text("setInterval(() => {}, 1000);")
    spawned: list[asyncio.subprocess.Process] = []
    real_exec = asyncio.create_subprocess_exec

    async def recording_exec(*args, **kwargs):
        proc = await real_exec(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(node_subprocess.asyncio, "create_subprocess_exec", recording_exec)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(run_node_script(script, b""), timeout=1)

    (proc,) = spawned
    assert proc.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(proc.pid, 0)


@pytest.mark.asyncio
async def test_early_stdin_close_preserves_node_oom_diagnostic(tmp_path):
    script = tmp_path / "early-exit.js"
    script.write_text(
        "process.stdin.destroy();process.stderr.write('FATAL ERROR: JavaScript heap out of memory');process.exit(134);"
    )
    code, _, stderr = await run_node_script(script, b"x" * (1024 * 1024))
    assert code == 134
    assert stderr.decode() == app_build_memory_message()


@pytest.mark.asyncio
async def test_output_overflow_kills_and_reaps_the_child(tmp_path, monkeypatch):
    script = tmp_path / "noisy.js"
    script.write_text("process.stdout.write('x'.repeat(4096));setInterval(() => {}, 1000);")
    policy = NodeScriptPolicy("test output", 1024, 128, 5, 1, 64, 1)
    spawned: list[asyncio.subprocess.Process] = []
    real_exec = asyncio.create_subprocess_exec

    async def recording_exec(*args, **kwargs):
        proc = await real_exec(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(node_subprocess.asyncio, "create_subprocess_exec", recording_exec)

    with pytest.raises(NodeScriptOutputLimitError):
        await run_node_script(script, b"", policy=policy)

    (proc,) = spawned
    assert proc.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(proc.pid, 0)


@pytest.mark.asyncio
async def test_policy_timeout_kills_and_reaps_the_child(tmp_path):
    script = tmp_path / "hang.js"
    script.write_text("setInterval(() => {}, 1000);")
    policy = NodeScriptPolicy("test timeout", 1024, 1024, 0.1, 1, 64, 1)

    with pytest.raises(NodeScriptTimeoutError):
        await run_node_script(script, b"", policy=policy)


@pytest.mark.asyncio
async def test_mixed_tools_share_the_process_wide_admission_cap(tmp_path):
    script = tmp_path / "sleep.js"
    script.write_text("setTimeout(() => process.exit(0), 350);")
    compiler_policy = NodeScriptPolicy("test compiler", 1024, 1024, 5, 1, 64, 2)
    tailwind_policy = NodeScriptPolicy("test tailwind", 1024, 1024, 5, 1, 64, 2)

    started = asyncio.get_running_loop().time()
    await asyncio.gather(
        run_node_script(script, b"", policy=compiler_policy),
        run_node_script(script, b"", policy=compiler_policy),
        run_node_script(script, b"", policy=tailwind_policy),
    )

    assert asyncio.get_running_loop().time() - started >= 0.55


@pytest.mark.asyncio
async def test_admission_timeout_releases_the_tool_slot(tmp_path, monkeypatch):
    script = tmp_path / "exit.js"
    script.write_text("process.exit(0);")
    process_slots = threading.BoundedSemaphore(1)
    process_slots.acquire()
    monkeypatch.setattr(node_subprocess, "_process_admission", process_slots)
    policy = NodeScriptPolicy("busy tool", 1024, 1024, 5, 0.01, 64, 1)

    with pytest.raises(NodeScriptAdmissionError):
        await run_node_script(script, b"", policy=policy)

    process_slots.release()
    assert await run_node_script(script, b"", policy=policy) == (0, b"", b"")


@pytest.mark.asyncio
async def test_cancelled_admission_releases_the_tool_slot(tmp_path, monkeypatch):
    script = tmp_path / "exit.js"
    script.write_text("process.exit(0);")
    process_slots = threading.BoundedSemaphore(1)
    process_slots.acquire()
    monkeypatch.setattr(node_subprocess, "_process_admission", process_slots)
    waiting = asyncio.Event()
    acquire_process_slot = node_subprocess._acquire_process_slot

    async def record_admission(timeout_seconds):
        waiting.set()
        await acquire_process_slot(timeout_seconds)

    monkeypatch.setattr(node_subprocess, "_acquire_process_slot", record_admission)
    policy = NodeScriptPolicy("cancelled tool", 1024, 1024, 5, 1, 64, 1)
    task = asyncio.create_task(run_node_script(script, b"", policy=policy))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.gather(task)

    process_slots.release()
    assert await run_node_script(script, b"", policy=policy) == (0, b"", b"")
