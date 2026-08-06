"""Transaction-pool-safe leader election for scheduler trigger ownership."""

from __future__ import annotations

import os
import socket
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, update
from sqlalchemy.dialects.postgresql import insert

from src.core.database import get_db_context
from src.models.orm.scheduler_leases import SchedulerLease

TRIGGER_LEASE_NAME = "scheduler-triggers"
DEFAULT_LEASE_DURATION = timedelta(seconds=30)


class SchedulerLeadershipLease:
    """Acquire and renew the singleton lease for APScheduler and pub/sub."""

    def __init__(
        self,
        *,
        owner_id: str | None = None,
        lease_duration: timedelta = DEFAULT_LEASE_DURATION,
    ) -> None:
        self.owner_id = owner_id or f"{socket.gethostname()}:{os.getpid()}"
        self.lease_duration = lease_duration
        self.lease_token: UUID | None = None

    @property
    def is_leader(self) -> bool:
        return self.lease_token is not None

    async def try_acquire(self) -> bool:
        """Atomically acquire the trigger lease when it is unowned or expired."""
        candidate_token = uuid4()
        async with get_db_context() as db:
            await db.execute(
                insert(SchedulerLease)
                .values(name=TRIGGER_LEASE_NAME, updated_at=func.now())
                .on_conflict_do_nothing(index_elements=[SchedulerLease.name])
            )
            acquired = (
                await db.execute(
                    update(SchedulerLease)
                    .where(
                        SchedulerLease.name == TRIGGER_LEASE_NAME,
                        or_(
                            SchedulerLease.lease_expires_at.is_(None),
                            SchedulerLease.lease_expires_at <= func.now(),
                        ),
                    )
                    .values(
                        owner_id=self.owner_id,
                        lease_token=candidate_token,
                        lease_expires_at=func.now() + self.lease_duration,
                        updated_at=func.now(),
                    )
                    .returning(SchedulerLease.lease_token)
                )
            ).scalar_one_or_none()

        if acquired is None:
            return False
        self.lease_token = acquired
        return True

    async def renew(self) -> bool:
        """Renew only a still-current, unexpired lease generation."""
        token = self.lease_token
        if token is None:
            return False

        async with get_db_context() as db:
            renewed = (
                await db.execute(
                    update(SchedulerLease)
                    .where(
                        SchedulerLease.name == TRIGGER_LEASE_NAME,
                        SchedulerLease.owner_id == self.owner_id,
                        SchedulerLease.lease_token == token,
                        SchedulerLease.lease_expires_at > func.now(),
                    )
                    .values(
                        lease_expires_at=func.now() + self.lease_duration,
                        updated_at=func.now(),
                    )
                    .returning(SchedulerLease.lease_token)
                )
            ).scalar_one_or_none()

        if renewed is None:
            self.lease_token = None
            return False
        return True

    async def release(self) -> None:
        """Release this lease generation without disturbing a newer leader."""
        token = self.lease_token
        self.lease_token = None
        if token is None:
            return

        async with get_db_context() as db:
            await db.execute(
                update(SchedulerLease)
                .where(
                    and_(
                        SchedulerLease.name == TRIGGER_LEASE_NAME,
                        SchedulerLease.owner_id == self.owner_id,
                        SchedulerLease.lease_token == token,
                    )
                )
                .values(
                    owner_id=None,
                    lease_token=None,
                    lease_expires_at=None,
                    updated_at=func.now(),
                )
            )


__all__ = [
    "DEFAULT_LEASE_DURATION",
    "SchedulerLeadershipLease",
    "TRIGGER_LEASE_NAME",
]
