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

Parent communication (ready flag, stop key, and for legacy-format attempts
the rotation token) travels over Redis keys owned by the worker parent —
this module is process-local only and never touches the network. ``ready()`` sets a thread-safe flag the
engine supervisor sends over the authenticated attempt transport; the stop
event is an ``asyncio.Event``
the engine sets on SIGTERM or when the parent mirrors ``stop_requested``.
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any, Awaitable, Callable

_ready_flag = threading.Event()
_stop_event: asyncio.Event | None = None
# A claims-format attempt's run refresh token, once installed. A token the
# parent rotates in never replaces it (see install_legacy_service_token).
_run_refresh_token: str | None = None


def install_service_credentials(access_token: str, refresh_token: str) -> bool:
    """Install a claims-format attempt's credentials for the SDK.

    The access token carries the attempt's authorization claims; the run
    refresh token lets the SDK refresh it on the API's challenge for as long
    as the attempt is live. Nothing rotates them from the parent. Returns
    False when either is empty.
    """
    global _run_refresh_token
    if not access_token or not refresh_token:
        return False
    _run_refresh_token = refresh_token
    _install(access_token, refresh_token)
    return True


def install_legacy_service_token(token: str) -> bool:
    """Install a legacy-format attempt's token (token PR1c deletes this).

    The refresh slot holds the token itself; the parent rotates it through
    the attempt's ``service-control`` handoff before it expires. Returns
    False when empty.

    In a claims-format attempt (its run refresh token installed) this never
    replaces the run refresh token: only the access slot changes, so the
    attempt can still refresh after a rollback to an older API that rotates
    every attempt and a roll forward that stops rotating it again.
    """
    if not token:
        return False
    _install(token, _run_refresh_token or token)
    return True


def _install(access_token: str, refresh_token: str) -> None:
    """Mirror the engine-credential handoff: the SDK's process backend takes
    precedence over keyring/JSON persistence and is updated in place when the
    SDK refreshes. Inside the attempt's execution context both tokens are
    registered for output redaction."""
    from bifrost._context import register_secret

    api_url = os.getenv("BIFROST_API_URL", "http://api:8000")
    os.environ["BIFROST_API_URL"] = api_url
    os.environ["BIFROST_ACCESS_TOKEN"] = access_token
    os.environ["BIFROST_REFRESH_TOKEN"] = refresh_token
    register_secret(access_token)
    register_secret(refresh_token)
    # The SDK caches its client after the first call. Updating only the
    # environment leaves that client's HTTP transports using an expired token.
    from bifrost.client import _install_cached_access_token

    _install_cached_access_token(api_url, access_token)


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
    global _stop_event, _run_refresh_token
    _stop_event = None
    _run_refresh_token = None
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
