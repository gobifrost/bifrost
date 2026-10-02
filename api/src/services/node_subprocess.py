"""Run the platform's Node helper scripts (bundle.js, compile.js, tailwind.js)."""

from __future__ import annotations

import asyncio
from pathlib import Path


async def run_node_script(script: Path, input_data: bytes) -> tuple[int | None, bytes, bytes]:
    """Run ``node <script>`` with ``input_data`` on stdin; return (returncode, stdout, stderr).

    If the caller is cancelled (request disconnect, ``asyncio.wait_for``
    timeout, shutdown) the child is killed and reaped instead of being left
    running after the awaiting coroutine is gone.
    """
    proc = await asyncio.create_subprocess_exec(
        "node", str(script),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await proc.communicate(input=input_data)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    return proc.returncode, stdout, stderr
