"""run_node_script never leaves a node child running after its caller is gone."""

import asyncio
import os

import pytest

from src.services import node_subprocess
from src.services.node_subprocess import run_node_script


@pytest.mark.asyncio
async def test_returns_exit_code_and_output(tmp_path):
    script = tmp_path / "echo.js"
    script.write_text(
        "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{process.stdout.write(s.toUpperCase());process.exit(3)});"
    )
    assert await run_node_script(script, b"hi") == (3, b"HI", b"")


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
