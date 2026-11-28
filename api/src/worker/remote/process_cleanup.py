"""Terminate and reap one launcher-owned new-session subprocess group."""
from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from typing import Protocol


class GroupProcess(Protocol):
    @property
    def pid(self) -> int | None: ...

    @property
    def returncode(self) -> int | None: ...

    async def wait(self) -> int: ...


def _reap_group(group: int) -> None:
    # The primary is already reaped by asyncio. PID1/subreapers own adopted
    # descendants; wait only for this operation's group, never unrelated jobs.
    while True:
        try:
            os.waitpid(-group, 0)
        except ChildProcessError:
            return


async def kill_process_group(process: GroupProcess) -> None:
    if process.pid is None:
        return
    group = process.pid
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(group, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            pass
    # A primary exiting on TERM does not imply that descendants exited.
    # Also clean a group whose primary finished before release was requested.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(group, signal.SIGKILL)
    await process.wait()
    await asyncio.to_thread(_reap_group, group)
