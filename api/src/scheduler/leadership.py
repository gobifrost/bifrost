"""Transaction-pool-safe leader election for scheduler trigger ownership.

The lease row is the only authority. Its expiry is computed in database time;
the holder additionally keeps a conservative local deadline (measured from
before each acquire/renew request) so it stops producing triggers before the
database could hand the lease to another replica.
"""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, update
from sqlalchemy.dialects.postgresql import insert

from src.core.database import get_db_context
from src.models.orm.scheduler_leases import SchedulerLease

TRIGGER_LEASE_NAME = "scheduler-triggers"
DEFAULT_LEASE_DURATION = timedelta(seconds=30)
# Margin for clock-rate drift between this process and PostgreSQL.
LOCAL_DEADLINE_MARGIN_SECONDS = 2.0


def control_owner_id() -> str:
    """Stable identity of this process in leases and Diagnostics."""
    return f"{socket.gethostname()}:{os.getpid()}"


class SchedulerLeadershipLease:
    """Acquire and renew the singleton lease for APScheduler and pub/sub."""

    def __init__(
        self,
        *,
        owner_id: str | None = None,
        name: str = TRIGGER_LEASE_NAME,
        lease_duration: timedelta = DEFAULT_LEASE_DURATION,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.owner_id = owner_id or control_owner_id()
        self.name = name
        self.lease_duration = lease_duration
        self.lease_token: UUID | None = None
        self._monotonic = monotonic
        self._valid_until: float | None = None

    @property
    def is_leader(self) -> bool:
        return self.lease_token is not None

    def seconds_remaining(self) -> float:
        """Conservative time left before another replica could acquire."""
        if self.lease_token is None or self._valid_until is None:
            return 0.0
        return max(0.0, self._valid_until - self._monotonic())

    def holds_lease(self) -> bool:
        """True only while this generation is certainly still current."""
        return self.seconds_remaining() > 0

    def _record_validity(self, requested_at: float) -> None:
        self._valid_until = (
            requested_at
            + self.lease_duration.total_seconds()
            - LOCAL_DEADLINE_MARGIN_SECONDS
        )

    async def try_acquire(self) -> bool:
        """Atomically acquire the trigger lease when it is unowned or expired."""
        candidate_token = uuid4()
        requested_at = self._monotonic()
        async with get_db_context() as db:
            await db.execute(
                insert(SchedulerLease)
                .values(name=self.name, updated_at=func.now())
                .on_conflict_do_nothing(index_elements=[SchedulerLease.name])
            )
            acquired = (
                await db.execute(
                    update(SchedulerLease)
                    .where(
                        SchedulerLease.name == self.name,
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
        self._record_validity(requested_at)
        return True

    async def renew(self) -> bool:
        """Renew only a still-current, unexpired lease generation."""
        token = self.lease_token
        if token is None:
            return False

        requested_at = self._monotonic()
        async with get_db_context() as db:
            renewed = (
                await db.execute(
                    update(SchedulerLease)
                    .where(
                        SchedulerLease.name == self.name,
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
            self._valid_until = None
            return False
        self._record_validity(requested_at)
        return True

    async def release(self) -> None:
        """Release this lease generation without disturbing a newer leader."""
        token = self.lease_token
        self.lease_token = None
        self._valid_until = None
        if token is None:
            return

        async with get_db_context() as db:
            await db.execute(
                update(SchedulerLease)
                .where(
                    and_(
                        SchedulerLease.name == self.name,
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
    "LOCAL_DEADLINE_MARGIN_SECONDS",
    "control_owner_id",
    "SchedulerLeadershipLease",
    "TRIGGER_LEASE_NAME",
]
