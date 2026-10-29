"""Database-clock fencing for a remote launcher's API control owner."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, or_, select, text, update

from src.core.database import get_liveness_db_context
from src.models.orm.execution_workers import ExecutionWorker


DEFAULT_OWNER_LEASE_SECONDS = 20


@dataclass(frozen=True)
class RemoteOwnerLease:
    worker_id: UUID
    owner_id: UUID
    launcher_id: UUID
    session_generation: int


async def acquire_remote_owner(
    *,
    worker_id: UUID,
    owner_id: UUID,
    launcher_id: UUID,
    nonce_digest: str,
    hello_sequence: int,
    lease_seconds: int = DEFAULT_OWNER_LEASE_SECONDS,
) -> RemoteOwnerLease | None:
    """Acquire one fenced API owner session for an admitted launcher.

    PostgreSQL decides expiry.  A new API owner may only replace an expired
    one; the current owner may rotate its own session on reconnect.  Every
    accepted hello consumes its nonce, preventing a captured hello from being
    replayed after a replica restart.
    """
    if lease_seconds <= 0:
        raise ValueError("remote owner lease must be positive")
    if hello_sequence < 1:
        raise ValueError("remote hello sequence must be positive")
    interval = f"{lease_seconds} seconds"
    async with get_liveness_db_context() as db:
        row = await db.execute(
            update(ExecutionWorker)
            .where(
                ExecutionWorker.id == worker_id,
                # Static launchers register a fresh canonical row before the
                # API-side executor has measured its admission budget. Jobs
                # are already ready after admit_worker; allowing ``starting``
                # here is therefore limited to the authenticated static path.
                ExecutionWorker.state.in_(("starting", "ready", "draining")),
                or_(
                    ExecutionWorker.remote_launcher_id.is_(None),
                    ExecutionWorker.remote_launcher_id == launcher_id,
                ),
                ExecutionWorker.remote_hello_sequence < hello_sequence,
                or_(
                    ExecutionWorker.remote_owner_id.is_(None),
                    ExecutionWorker.remote_owner_id == owner_id,
                    ExecutionWorker.remote_owner_lease_expires_at <= func.now(),
                ),
            )
            .values(
                remote_launcher_id=launcher_id,
                remote_owner_id=owner_id,
                remote_session_generation=ExecutionWorker.remote_session_generation + 1,
                remote_owner_lease_expires_at=func.now() + text(f"INTERVAL '{interval}'"),
                remote_auth_nonce_digest=nonce_digest,
                remote_hello_sequence=hello_sequence,
            )
            .returning(ExecutionWorker.remote_session_generation)
        )
        generation = row.scalar_one_or_none()
    if generation is None:
        return None
    return RemoteOwnerLease(worker_id, owner_id, launcher_id, generation)


async def renew_remote_owner(
    lease: RemoteOwnerLease, *, lease_seconds: int = DEFAULT_OWNER_LEASE_SECONDS
) -> bool:
    """Renew only the still-current owner generation using the DB clock."""
    if lease_seconds <= 0:
        raise ValueError("remote owner lease must be positive")
    interval = f"{lease_seconds} seconds"
    async with get_liveness_db_context() as db:
        renewed = await db.scalar(
            update(ExecutionWorker)
            .where(
                ExecutionWorker.id == lease.worker_id,
                ExecutionWorker.remote_owner_id == lease.owner_id,
                ExecutionWorker.remote_launcher_id == lease.launcher_id,
                ExecutionWorker.remote_session_generation == lease.session_generation,
                ExecutionWorker.remote_owner_lease_expires_at > func.now(),
            )
            .values(remote_owner_lease_expires_at=func.now() + text(f"INTERVAL '{interval}'"))
            .returning(ExecutionWorker.id)
        )
    return renewed is not None


async def owns_remote_session(lease: RemoteOwnerLease) -> bool:
    """Fence commands and result commits before they reach canonical consumers."""
    async with get_liveness_db_context() as db:
        worker_id = await db.scalar(
            select(ExecutionWorker.id).where(
                ExecutionWorker.id == lease.worker_id,
                ExecutionWorker.remote_owner_id == lease.owner_id,
                ExecutionWorker.remote_launcher_id == lease.launcher_id,
                ExecutionWorker.remote_session_generation == lease.session_generation,
                ExecutionWorker.remote_owner_lease_expires_at > func.now(),
            )
        )
    return worker_id is not None


async def release_remote_owner(lease: RemoteOwnerLease) -> bool:
    """Best-effort graceful release; stale owners cannot clear a newer fence."""
    async with get_liveness_db_context() as db:
        released = await db.scalar(
            update(ExecutionWorker)
            .where(
                ExecutionWorker.id == lease.worker_id,
                ExecutionWorker.remote_owner_id == lease.owner_id,
                ExecutionWorker.remote_session_generation == lease.session_generation,
            )
            .values(remote_owner_lease_expires_at=func.now())
            .returning(ExecutionWorker.id)
        )
    return released is not None
