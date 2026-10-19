"""Read successful recorded permission decisions for conversion proposals."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.audit import AuditLog, named_workflow_id


async def recorded_permission_requirements(
    db: AsyncSession, workflow_id: UUID, *, window_days: int
) -> list[str]:
    """Use successful recorded permission decisions, including elevated branches.

    A route template cannot identify permissions selected by request data,
    such as reading another user's private run. The recorded decision can.
    Both compact and expanded traces retain the same reached-step facts.
    """
    from src.models.contracts.permissions import parse_permission

    rows = await db.stream_scalars(
        select(AuditLog.details).where(
            AuditLog.action == "access.check",
            named_workflow_id() == str(workflow_id),
            AuditLog.created_at >= datetime.now(timezone.utc) - timedelta(days=window_days),
            AuditLog.outcome == "success",
        )
    )
    permissions: set[str] = set()
    async for details in rows:
        if not isinstance(details, dict):
            continue
        trace = details.get("trace") or {}
        for step in trace.get("steps", []):
            if step.get("key") not in {"permission", "secret", "privileged_target"} or step.get("status") != "passed":
                continue
            permission = (step.get("facts") or {}).get("permission")
            if not isinstance(permission, str):
                continue
            # The closed vocabulary excludes wildcard authority and stale or
            # malformed audit facts from becoming declaration candidates.
            try:
                parse_permission(permission)
            except ValueError:
                continue
            permissions.add(permission)
    await rows.close()
    return sorted(permissions)
