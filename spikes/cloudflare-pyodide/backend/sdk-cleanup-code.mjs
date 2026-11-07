// Physical completion adapter only; upstream still owns every client close.
export const sdkCleanupCode = `
import asyncio

_pending = set()

def _finished(task):
    _pending.discard(task)
    if not task.cancelled():
        task.exception()

async def close_sdk(client):
    task = asyncio.create_task(client.close())
    _pending.add(task)
    task.add_done_callback(_finished)
    await asyncio.shield(task)
`;
