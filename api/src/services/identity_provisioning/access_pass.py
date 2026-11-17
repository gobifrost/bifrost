"""The access pass: applies provisioning grants in batches.

Bifrost has one global authority epoch. Any committed change to a fact an
access token carries (a user's activity or name, a role assignment) replaces
it, and every access token on the instance must then refresh. A SCIM
initial sync sends one request per user or membership change, so applying
each grant in its own request would retire every token on the platform about
once a second for the length of the sync.

So provisioning applies revocations at once, in the request, and records
grants on the link (``IdentityProviderUser.access_pending_since``). This pass
runs every ``INTERVAL`` and applies the waiting grants across all connections
in one transaction: at most one epoch change per run, so grants add at most
30 an hour. Revocations stay in the request and count separately.

The pass is short leader-only scheduler work (``BATCH_LIMIT`` links, no
outside calls before the commit) and idempotent, so a failover rerun is
harmless.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database_retry import is_transaction_conflict
from src.models.orm.identity_providers import IdentityProvider, IdentityProviderUser
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.services.identity_provisioning.core import Provisioner

logger = logging.getLogger(__name__)

# How often the pass runs: the longest a grant waits.
INTERVAL = timedelta(minutes=2)
# Links applied per run; the rest wait for the next run, oldest first.
BATCH_LIMIT = 500


@dataclass
class AccessPassResult:
    applied: int = 0
    failed: int = 0
    # Links PostgreSQL rolled back to break a deadlock; they wait for the
    # next run.
    conflicts: int = 0
    # Provisioners to run ``after_commit`` on once the caller commits.
    provisioners: list[Provisioner] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {"applied": self.applied, "failed": self.failed, "conflicts": self.conflicts}


def _waiting(*columns: Any) -> Select[Any]:
    """Links waiting for a grant on an enabled connection in an active
    organization (a disabled one's grants wait until it's enabled again)."""
    return (
        select(*columns)
        .select_from(IdentityProviderUser)
        .join(IdentityProvider, IdentityProvider.id == IdentityProviderUser.identity_provider_id)
        .join(Organization, Organization.id == IdentityProvider.organization_id)
        .where(
            IdentityProviderUser.access_pending_since.is_not(None),
            IdentityProvider.is_enabled.is_(True),
            Organization.is_active.is_(True),
        )
    )


async def run_access_pass(session: AsyncSession) -> AccessPassResult:
    """Apply waiting grants; the caller commits, then runs each
    provisioner's ``after_commit``.

    The oldest waiting links go first, whatever their connection. The pass
    takes every involved connection's policy lock (shared, in id order)
    before touching any user or role, so it never holds one connection's
    rows while waiting on another's mapping change. Users a request holds
    are skipped until the next run. A link that fails rolls back alone and
    goes to the back of the queue.
    """
    candidates = (
        await session.execute(
            _waiting(IdentityProviderUser.id, IdentityProviderUser.identity_provider_id)
            .order_by(IdentityProviderUser.access_pending_since, IdentityProviderUser.id)
            .limit(BATCH_LIMIT)
        )
    ).all()
    result = AccessPassResult()
    if not candidates:
        return result
    provisioners: dict[UUID, Provisioner] = {}
    for connection_id in sorted({connection_id for _, connection_id in candidates}):
        connection = await session.get(IdentityProvider, connection_id)
        assert connection is not None
        provisioner = Provisioner(session, connection)
        await provisioner.hold_policy()
        provisioners[connection_id] = provisioner
    # Recheck under the locks: a request may have applied or cleared a link,
    # or a connection may have been disabled, since the candidates were read.
    locked = set(
        await session.scalars(
            _waiting(IdentityProviderUser.id)
            .join(User, User.id == IdentityProviderUser.user_id)
            .where(IdentityProviderUser.id.in_([link_id for link_id, _ in candidates]))
            .with_for_update(of=User, skip_locked=True)
        )
    )
    for link_id, connection_id in candidates:
        if link_id not in locked:
            continue
        provisioner = provisioners[connection_id]
        queued = len(provisioner.pending_events)
        stale_roles, stale_users = set(provisioner.stale_roles), set(provisioner.stale_users)
        try:
            async with session.begin_nested():
                await provisioner.apply_pending([link_id])
        except Exception as exc:
            del provisioner.pending_events[queued:]
            provisioner.stale_roles, provisioner.stale_users = stale_roles, stale_users
            if is_transaction_conflict(exc):
                # PostgreSQL broke a deadlock (e.g. with a mapping change
                # granting the same role) by rolling this link back.
                result.conflicts += 1
                logger.warning(
                    "Identity provider %s access for link %s was rolled back to break a "
                    "deadlock; it waits for the next run",
                    connection_id,
                    link_id,
                )
            else:
                result.failed += 1
                logger.exception(
                    "Identity provider %s could not apply access for link %s",
                    connection_id,
                    link_id,
                )
            await session.execute(
                update(IdentityProviderUser)
                .where(IdentityProviderUser.id == link_id)
                .values(access_pending_since=datetime.now(timezone.utc))
            )
            continue
        result.applied += 1
    result.provisioners = list(provisioners.values())
    return result
