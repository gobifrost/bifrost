"""Transaction-scoped fencing for a remote launcher's API control owner."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.execution_workers import ExecutionWorker
from src.worker.remote.ownership import RemoteOwnerLease


@dataclass
class LeaseRef:
    """Mutable lease shared by tasks for one remote launcher session.

    A same-owner reconnect advances the generation in PostgreSQL and replaces
    ``lease`` in this object. Tasks inherited from the original connection
    therefore fence against the current generation instead of retaining an
    obsolete immutable lease value.
    """

    lease: RemoteOwnerLease


_remote_lease_ref: ContextVar[LeaseRef | None] = ContextVar(
    "remote_owner_lease_ref", default=None
)


@contextmanager
def remote_owner_context(ref: LeaseRef) -> Iterator[None]:
    """Install ``ref`` for a remote API-owner task and its child tasks."""

    token: Token[LeaseRef | None] = _remote_lease_ref.set(ref)
    try:
        yield
    finally:
        _remote_lease_ref.reset(token)


def current_remote_lease_ref() -> LeaseRef | None:
    """Return the remote-owner context for this task, if it has one."""

    return _remote_lease_ref.get()


async def fence_remote_owner(db: AsyncSession, *, for_update: bool = False) -> bool:
    """Lock and validate the current remote API owner for this transaction.

    Ordinary API, scheduler, and launcher-child callers have no context and
    remain valid. A remote API-owner task must hold a shared lock on its
    still-current worker row until the caller commits its mutation. Owner
    replacement updates that row under PostgreSQL's exclusive row lock, so it
    serializes with the protected write instead of racing a prior ``EXISTS``
    check. Callers that will update the worker row itself request ``FOR
    UPDATE``; otherwise an owner heartbeat could deadlock by trying to
    upgrade its own shared lock.
    """

    ref = current_remote_lease_ref()
    if ref is None:
        return True
    lease = ref.lease
    worker_id = await db.scalar(
        select(ExecutionWorker.id)
        .where(
            ExecutionWorker.id == lease.worker_id,
            ExecutionWorker.remote_owner_id == lease.owner_id,
            ExecutionWorker.remote_launcher_id == lease.launcher_id,
            ExecutionWorker.remote_session_generation == lease.session_generation,
            ExecutionWorker.remote_owner_lease_expires_at > func.now(),
        )
        .with_for_update(read=not for_update)
    )
    return worker_id is not None


__all__ = [
    "LeaseRef",
    "current_remote_lease_ref",
    "fence_remote_owner",
    "remote_owner_context",
]
