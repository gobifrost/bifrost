"""Experimental one-coroutine adapter; no physical-isolate stop guarantee."""
import asyncio

_active = {}


def request_cancel(execution_id):
    slot = _active.get(execution_id)
    if slot is None or slot['task'].done():
        return False
    if slot['requested']:
        return True
    slot['requested'] = slot['task'].cancel()
    return slot['requested']


def active_execution_count():
    return len(_active)


async def execute_cooperatively(execution_id, execute):
    if _active:
        raise RuntimeError('Fresh single-execution child required')
    task = asyncio.create_task(execute())
    slot = {'task': task, 'requested': False}
    _active[execution_id] = slot
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
        _active.pop(execution_id)
