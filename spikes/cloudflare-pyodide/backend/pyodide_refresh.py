"""Cooperative physical refresh bridges for a fresh single-loop Pyodide run.

Upstream's core owns credential adoption, generation, persistence, redaction
and API authorization. Only physical serialization/await bridges change here.
"""
import asyncio

_locks: dict[str, asyncio.Lock] = {}
_pending: set[asyncio.Task] = set()


async def refresh_async(api_url: str, observed_access_token: str | None) -> str | None:
    from bifrost import client

    normalized = api_url.rstrip("/")

    async def coordinated():
        async with _locks.setdefault(normalized, asyncio.Lock()):
            coordinator = client._refresh_coordinator(normalized)
            return await client._refresh_locked(normalized, coordinator, observed_access_token)

    # Like upstream's worker thread, the critical refresh completes even if
    # its waiter is cancelled. Keep a strong reference until it finishes.
    task = asyncio.create_task(coordinated())
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    token = await asyncio.shield(task)
    client._register_refreshed(token)
    return token


def refresh_sync(api_url: str, observed_access_token: str) -> str | None:
    from pyodide.ffi import run_sync
    from bifrost import client

    token = run_sync(client.refresh_connection_access_token(api_url, observed_access_token))
    client._register_refreshed(token)
    return token


def install():
    from bifrost import client

    client._refresh_connection_access_token_sync = refresh_sync
    client.refresh_connection_access_token = refresh_async
