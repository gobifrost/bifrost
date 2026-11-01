"""Async task lifecycle support for isolated test fixtures, not executor termination."""
import asyncio


async def cancel_and_join(task):
    """Join cleanup while preserving parent cancellation and receiver failures."""
    task.cancel()
    outcome, = await asyncio.gather(task, return_exceptions=True)
    if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
        raise outcome
