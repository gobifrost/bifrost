"""
Service supervision SDK for Bifrost.

Process-local supervision state for ``@service`` executions. The engine's
service mode installs a stop event before invoking the user coroutine and
clears it afterwards; the ``ready`` / ``is_stopping`` / ``wait_until_stopping``
callables are attached to the ``service`` decorator object in
``bifrost/decorators.py`` and ``src/sdk/decorators.py`` so user code calls::

    from bifrost import service

    @service
    async def telegram_bridge() -> None:
        await service.ready()
        while not service.is_stopping():
            await poll_once()

Parent communication (ready flag, stop key, rotation token) travels over
Redis keys owned by the worker parent — this module is process-local only
and never touches the network. ``ready()`` sets a thread-safe flag the
engine supervisor drains to Redis; the stop event is an ``asyncio.Event``
the engine sets on SIGTERM or when the parent mirrors ``stop_requested``.
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any, Awaitable, Callable

_ready_flag = threading.Event()
_stop_event: asyncio.Event | None = None


def install_service_credentials(token: str) -> bool:
    """Install a service token as process-scoped SDK credentials.

    Mirrors the engine-token handoff: the SDK's process backend takes
    precedence over keyring/JSON persistence, and supports refresh by
    replacing the same two variables. Returns False when empty.
    """
    if not token:
        return False
    api_url = os.getenv("BIFROST_API_URL", "http://api:8000")
    os.environ["BIFROST_API_URL"] = api_url
    os.environ["BIFROST_ACCESS_TOKEN"] = token
    os.environ["BIFROST_REFRESH_TOKEN"] = token
    return True


def _get_stop_event() -> asyncio.Event:
    event = _stop_event
    if event is None:
        raise RuntimeError(
            "service supervision is only available inside an @service execution"
        )
    return event


def install_service_runtime(stop_event: asyncio.Event) -> None:
    """Install the stop event for one service run (engine service mode)."""
    global _stop_event
    _ready_flag.clear()
    _stop_event = stop_event


def clear_service_runtime() -> None:
    """Remove service supervision state (engine teardown)."""
    global _stop_event
    _stop_event = None
    _ready_flag.clear()


def take_ready_report() -> bool:
    """Consume the pending ready report, if any (engine supervisor)."""
    return _drain_ready()


def _drain_ready() -> bool:
    if _ready_flag.is_set():
        _ready_flag.clear()
        return True
    return False


async def ready() -> None:
    """Report the service ready: starting → running.

    The engine supervisor notices the flag on its next tick and records
    ``ready_at`` on the attempt. Raises outside an ``@service`` execution.
    """
    _get_stop_event()
    _ready_flag.set()


def is_stopping() -> bool:
    """True once a stop was requested (SIGTERM or parent stop mirror)."""
    event = _stop_event
    return event is not None and event.is_set()


async def wait_until_stopping() -> None:
    """Resolve when a stop is requested. Raises outside ``@service``."""
    await _get_stop_event().wait()


def attach_service_runtime(namespace: Any) -> None:
    """Attach the supervision callables to the ``service`` decorator object."""
    namespace.ready = ready
    namespace.is_stopping = is_stopping
    namespace.wait_until_stopping = wait_until_stopping


# Type lobby for static use without an installed runtime.
ReadyCallback = Callable[[], Awaitable[None]]
