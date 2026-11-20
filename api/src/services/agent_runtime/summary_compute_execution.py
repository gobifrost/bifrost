"""Select pure local or admitted physical compute for API-owned summaries."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from shared.agent_summary_compute import (
    SummaryCompletionCallback,
    SummaryComputeInput,
    SummaryComputeResult,
    compute_summary as compute_local_summary,
)

SummaryComputeBuilder = Callable[
    [SummaryComputeInput, SummaryCompletionCallback], Awaitable[SummaryComputeResult]
]
_builder: ContextVar[SummaryComputeBuilder | None] = ContextVar("summary_compute_builder", default=None)
_receipt: ContextVar[UUID | None] = ContextVar("summary_compute_receipt", default=None)


@contextmanager
def summary_compute_execution(builder: SummaryComputeBuilder) -> Iterator[None]:
    token = _builder.set(builder)
    try:
        yield
    finally:
        _builder.reset(token)


@contextmanager
def summary_compute_receipt(receipt_id: UUID) -> Iterator[None]:
    token = _receipt.set(receipt_id)
    try:
        yield
    finally:
        _receipt.reset(token)


def current_summary_compute_receipt() -> UUID | None:
    return _receipt.get()


def is_summary_compute_execution() -> bool:
    return _builder.get() is not None


async def compute_summary(
    summary_input: SummaryComputeInput, complete: SummaryCompletionCallback,
) -> SummaryComputeResult:
    builder = _builder.get()
    if builder is None:
        return await compute_local_summary(summary_input, complete)
    return await builder(summary_input, complete)
