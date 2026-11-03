"""Experimental one-coroutine adapter; no physical-isolate stop guarantee."""
import asyncio
from contextlib import suppress

_active = {}


def request_cancel(execution_id):
    slot = _active.get(execution_id)
    if slot is None or slot['task'].done():
        return False
    if slot['requested']:
        return True
    slot['requested'] = True if slot['owner_scheduled'] else slot['task'].cancel()
    return slot['requested']


def active_execution_count():
    return len(_active)


async def execute_cooperatively(execution_id, execute, *, owner_scheduled=False):
    if _active:
        raise RuntimeError('Fresh single-execution child required')
    task = asyncio.create_task(execute())
    slot = {'task': task, 'requested': False, 'owner_scheduled': owner_scheduled}
    _active[execution_id] = slot

    async def cancel_from_owner():
        # The timer and cancel call originate in the execution invocation,
        # rather than capturing the separate control RPC's I/O context.
        while not task.done():
            await asyncio.sleep(0.01)
            if slot['requested']:
                task.cancel()
                return

    monitor = asyncio.create_task(cancel_from_owner()) if owner_scheduled else None
    try:
        return await task
    except asyncio.CancelledError:
        if not slot['requested'] or not task.done() or not task.cancelled():
            raise
        # This proves only that the tracked coroutine settled as cancelled.
        # Untracked tasks, native work, resource limits and isolate lifecycle
        # remain separate acceptance requirements. No user exception text.
        # Deliberately not a BiFrost terminal-result envelope: spawned work
        # can still run even after the tracked task's finally blocks finish.
        return {'cooperative_task_cancelled': True,
                'physical_stopped': False, 'execution_settled': False}
    finally:
        if monitor is not None:
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor
        _active.pop(execution_id)
