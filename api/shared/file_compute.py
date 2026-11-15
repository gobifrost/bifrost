"""Typed API-to-launcher contract for registered file-backed computation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from shared.worker_transfer import FileTransfer, ReceivedFileTransfer


@dataclass
class FileComputeResult:
    """A verified result transfer whose temporary payload is caller-owned."""

    transfer: ReceivedFileTransfer
    metadata: Mapping[str, Any]

    def close(self) -> None:
        self.transfer.close()

    def cancel(self) -> None:
        self.transfer.cancel()

    def __enter__(self) -> "FileComputeResult":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


FileCompute = Callable[[str, FileTransfer], Awaitable[FileComputeResult]]

T = TypeVar("T")
logger = logging.getLogger(__name__)


async def encode_file_transfer(
    encoder: Callable[..., FileTransfer], *args: Any, **kwargs: Any
) -> FileTransfer:
    """Offload an encoder without orphaning its transfer if awaiting is cancelled."""
    task = asyncio.create_task(asyncio.to_thread(encoder, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        def close_produced_transfer(done: asyncio.Task[FileTransfer]) -> None:
            try:
                transfer = done.result()
            except (Exception, asyncio.CancelledError):
                # The cancelled caller cannot safely observe a late producer
                # failure.  Consume it without logging encoder input details.
                return
            try:
                transfer.close()
            except Exception as exc:
                logger.warning(
                    "Cancelled file-transfer cleanup failed (%s)", type(exc).__name__,
                )

        task.add_done_callback(close_produced_transfer)
        raise


async def decode_file_result(
    outcome: FileComputeResult,
    decoder: Callable[..., T],
    cleanup_cancelled_result: Callable[[T], None] | None = None,
    **kwargs: Any,
) -> T:
    """Decode a result while retaining its received file until thread completion."""
    def decode_and_close() -> T:
        with outcome:
            return decoder(outcome.transfer, **kwargs)

    task = asyncio.create_task(asyncio.to_thread(decode_and_close))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        def consume_late_decode(done: asyncio.Task[T]) -> None:
            try:
                result = done.result()
            except (Exception, asyncio.CancelledError):
                # Cleanup happened in ``decode_and_close``; avoid surfacing a
                # late decoder failure into an already-cancelled request.
                pass
            else:
                if cleanup_cancelled_result is not None:
                    cleanup_cancelled_result(result)

        task.add_done_callback(consume_late_decode)
        raise


async def run_owned_file_io(operation: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Join an owned file operation before cancellation can close its handles."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            if not cancelled:
                raise
    if cancelled:
        # Consume a late failure without exposing input/crypto details.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()
