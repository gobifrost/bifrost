"""Authorization scope parsing shared by organization-backed reports."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status

from src.services.authorization.enforce import Caller, operation_reach, org_target, require_operation
from src.services.authorization.evaluator import GLOBAL
from src.services.authorization.reach import OrgReach


def report_scope(caller: Caller, operation: str, requested_scope: str | UUID | None) -> OrgReach:
    """Return the caller's permitted report rows for an optional scope.

    An omitted scope is a collection read and keeps the operation's original-user
    reach. A specific organization and Global are actual targets, so each gets
    an operation decision before the report queries its aggregates.
    """
    reach = operation_reach(caller, operation)
    if requested_scope is None:
        return reach
    if requested_scope == "global":
        require_operation(caller, operation, GLOBAL)
        return OrgReach(include_global=True)
    try:
        organization_id = requested_scope if isinstance(requested_scope, UUID) else UUID(requested_scope)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid scope value: {requested_scope}",
        ) from exc
    require_operation(caller, operation, org_target(organization_id))
    return OrgReach(organization_ids=frozenset({organization_id}))
