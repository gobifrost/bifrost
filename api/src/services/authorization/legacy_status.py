"""What still depends on legacy-format runtime credentials (token PR1c deletes this).

Legacy runtime tokens are bridged only while their run is live, and only
remote launchers that report no credential protocol (or protocol 1) still
receive them. Token PR1c removes the bridge behind a migration precondition
over these same two facts: no live run whose row records the legacy format
(``credential_version IS NULL``), and no remote launcher with a live owner
lease on a protocol below the claims protocol. Operators read them first with
``bifrost admin authz legacy-status``.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.authz import LegacyCredentialStatus, LegacyLauncher, LegacyRuntimeRun
from src.models.enums import LIVE_EXECUTION_STATUSES
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.executions import Execution
from src.models.orm.services import _SERVICE_LIVE_STATES, ServiceAttempt
from src.services.authorization.tokens import CLAIMS_CREDENTIAL_PROTOCOL


async def legacy_credential_status(db: AsyncSession) -> LegacyCredentialStatus:
    """Live legacy-format runs and connected legacy launchers, read-only."""
    executions = (
        await db.execute(
            select(
                Execution.id,
                Execution.workflow_name,
                Execution.status,
                Execution.organization_id,
                Execution.started_at,
            )
            .where(Execution.status.in_(LIVE_EXECUTION_STATUSES), Execution.credential_version.is_(None))
            .order_by(Execution.started_at, Execution.id)
        )
    ).all()
    attempts = (
        await db.execute(
            select(ServiceAttempt.id, ServiceAttempt.service_id, ServiceAttempt.state, ServiceAttempt.started_at)
            .where(
                ServiceAttempt.state.in_(_SERVICE_LIVE_STATES),
                ServiceAttempt.lease_expires_at > func.now(),
                ServiceAttempt.credential_version.is_(None),
            )
            .order_by(ServiceAttempt.started_at, ServiceAttempt.id)
        )
    ).all()
    launchers = (
        await db.execute(
            select(
                ExecutionWorker.id,
                ExecutionWorker.hostname,
                ExecutionWorker.state,
                ExecutionWorker.remote_credential_protocol,
            )
            .where(
                ExecutionWorker.remote_owner_lease_expires_at > func.now(),
                or_(
                    ExecutionWorker.remote_credential_protocol.is_(None),
                    ExecutionWorker.remote_credential_protocol < CLAIMS_CREDENTIAL_PROTOCOL,
                ),
            )
            .order_by(ExecutionWorker.hostname, ExecutionWorker.id)
        )
    ).all()
    return LegacyCredentialStatus(
        live_runs=[
            *(
                LegacyRuntimeRun(
                    kind="workflow_execution",
                    id=row.id,
                    name=row.workflow_name,
                    status=row.status.value,
                    organization_id=row.organization_id,
                    started_at=row.started_at,
                )
                for row in executions
            ),
            *(
                LegacyRuntimeRun(
                    kind="service_attempt",
                    id=row.id,
                    name=str(row.service_id),
                    status=row.state,
                    started_at=row.started_at,
                )
                for row in attempts
            ),
        ],
        launchers=[
            LegacyLauncher(
                worker_id=row.id,
                hostname=row.hostname,
                state=row.state,
                credential_protocol=row.remote_credential_protocol or 1,
            )
            for row in launchers
        ],
    )
