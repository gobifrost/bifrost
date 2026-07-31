"""Standalone entry point for the credential-light build coordinator."""

from __future__ import annotations

import asyncio
import signal

from src.builder.coordinator import (
    BuilderCoordinator,
    CoordinatorSettings,
    assert_secretless_environment,
    heartbeat_forever,
)


async def main() -> None:
    assert_secretless_environment()
    settings = CoordinatorSettings.from_env()
    coordinator = BuilderCoordinator(settings)
    await coordinator.start()
    heartbeat = asyncio.create_task(heartbeat_forever(settings))
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    try:
        await stopped.wait()
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        await coordinator.drain(deadline=settings.builder_build_timeout_s + 30)


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
