"""Usage-limit policy management and read models."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.ai_usage import (
    UsageLimitEffectiveResponse,
    UsageLimitListResponse,
    UsageLimitPolicyPublic,
    UsageLimitPolicyUpsert,
)
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.services.audit import emit_audit
from src.services.authorization import (
    AuthorizationBoundaryKind,
    CurrentAuthorizationContext,
)
from src.services.builder.private_solutions import load_accessible_private_solution
from src.services.solutions.access import VISIBILITY_PRIVATE, SolutionAction
from src.services.usage_limits import (
    UsageCeilings,
    UsageLimitPeriod,
    UsageLimitScope,
    UsageLimitSubject,
    delete_usage_limit_policy,
    list_usage_limit_policies_for_boundary,
    read_effective_usage_limits,
    upsert_usage_limit_policy,
    usage_subject_for_scope,
)

router = APIRouter(prefix="/api/settings/ai/usage-limits", tags=["AI Settings"])


async def _private_solution_usage_organization_id(
    db: AsyncSession,
    solution: Solution,
) -> UUID | None:
    """Return the organization budget hierarchy for a private Solution.

    New private Builder Solutions store the selected organization on the
    Solution row. Older/personal rows may rely on the owner's home
    organization. Ownerless/orgless private rows fail closed at callers.
    """

    if solution.organization_id is not None:
        return solution.organization_id
    if solution.owner_user_id is None:
        return None
    return await db.scalar(
        select(User.organization_id).where(User.id == solution.owner_user_id)
    )


def _selected_boundary_org(
    authorization: CurrentAuthorizationContext,
) -> UUID | None:
    boundary = authorization.selected_boundary
    if boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select an exact Platform or Organization boundary",
        )
    if boundary.kind is AuthorizationBoundaryKind.PLATFORM:
        return None
    return boundary.organization_id


def _parse_scope(scope: str) -> UsageLimitScope:
    try:
        return UsageLimitScope(scope)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scope must be platform, organization, user, or solution",
        ) from exc


def _parse_target_id(scope: UsageLimitScope, target_id: str) -> UUID | None:
    if scope is UsageLimitScope.PLATFORM:
        if target_id != "platform":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Platform usage-limit target_id must be platform",
            )
        return None
    try:
        return UUID(target_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{scope.value} usage-limit target_id must be a UUID",
        ) from exc


def _require_exact_boundary(
    authorization: CurrentAuthorizationContext,
    *,
    organization_id: UUID | None,
) -> None:
    boundary = authorization.selected_boundary
    if organization_id is None:
        matches = boundary.kind is AuthorizationBoundaryKind.PLATFORM
    else:
        matches = (
            boundary.kind is AuthorizationBoundaryKind.ORGANIZATION
            and boundary.organization_id == organization_id
        )
    if matches:
        return
    expected = "platform" if organization_id is None else f"organization:{organization_id}"
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "The selected authorization boundary does not match this usage-limit "
            f"target; select {expected}"
        ),
    )


async def _resolve_policy_subject(
    db: AsyncSession,
    authorization: CurrentAuthorizationContext,
    *,
    scope: UsageLimitScope,
    target_id: UUID | None,
) -> UsageLimitSubject:
    if scope is UsageLimitScope.PLATFORM:
        _require_exact_boundary(authorization, organization_id=None)
        return usage_subject_for_scope(scope)

    if scope is UsageLimitScope.ORGANIZATION:
        assert target_id is not None
        _require_exact_boundary(authorization, organization_id=target_id)
        return usage_subject_for_scope(scope, organization_id=target_id)

    if scope is UsageLimitScope.USER:
        assert target_id is not None
        row = await db.get(User, target_id)
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        _require_exact_boundary(authorization, organization_id=row.organization_id)
        return usage_subject_for_scope(
            scope,
            organization_id=row.organization_id,
            user_id=row.id,
        )

    assert target_id is not None
    solution = await db.get(Solution, target_id)
    if solution is None:
        raise HTTPException(status_code=404, detail="Solution not found")
    organization_id = solution.organization_id
    if solution.visibility == VISIBILITY_PRIVATE:
        organization_id = await _private_solution_usage_organization_id(db, solution)
        if organization_id is None:
            raise HTTPException(status_code=404, detail="Solution not found")
    _require_exact_boundary(authorization, organization_id=organization_id)
    return usage_subject_for_scope(
        scope,
        organization_id=organization_id,
        solution_id=solution.id,
    )


async def _private_solution_usage_subject_if_admitted(
    db: AsyncSession,
    authorization: CurrentAuthorizationContext,
    current_user: CurrentActiveUser,
    solution_id: UUID,
    *,
    action: SolutionAction,
) -> UsageLimitSubject | None:
    loaded = await load_accessible_private_solution(
        db,
        solution_id=solution_id,
        action=action,
        actor_user_id=current_user.user_id,
        is_platform_admin=authorization.has_capability("platform.superuser"),
        is_external=current_user.is_external,
        can_support=authorization.has_delegated_capability("builder.read"),
        effective_role_ids=frozenset(authorization.role_ids),
    )
    if loaded is None:
        return None
    solution, _project = loaded
    organization_id = await _private_solution_usage_organization_id(db, solution)
    if organization_id is None:
        return None
    _require_exact_boundary(authorization, organization_id=organization_id)
    return usage_subject_for_scope(
        UsageLimitScope.SOLUTION,
        organization_id=organization_id,
        solution_id=solution.id,
    )


async def _authorize_effective_read(
    db: AsyncSession,
    authorization: CurrentAuthorizationContext,
    current_user: CurrentActiveUser,
    *,
    scope: UsageLimitScope,
    target_id: UUID | None,
) -> UsageLimitSubject:
    if scope is UsageLimitScope.SOLUTION and target_id is not None:
        solution = await db.get(Solution, target_id)
        if solution is None:
            raise HTTPException(status_code=404, detail="Solution not found")
        if solution.visibility == VISIBILITY_PRIVATE:
            private_subject = await _private_solution_usage_subject_if_admitted(
                db,
                authorization,
                current_user,
                target_id,
                action=SolutionAction.VIEW,
            )
            if private_subject is None:
                raise HTTPException(status_code=404, detail="Solution not found")
            return private_subject

    subject = await _resolve_policy_subject(
        db,
        authorization,
        scope=scope,
        target_id=target_id,
    )
    if authorization.has_capability("metrics.read"):
        return subject
    if scope is UsageLimitScope.USER and target_id == current_user.user_id:
        return subject
    if scope is UsageLimitScope.SOLUTION:
        assert target_id is not None
        solution = await db.get(Solution, target_id)
        if solution is None:
            raise HTTPException(status_code=404, detail="Solution not found")
        if not authorization.has_capability(
            "solutions.read"
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing required capability: metrics.read",
            )
        return subject
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Missing required capability: metrics.read",
    )


@router.get("", response_model=UsageLimitListResponse)
async def list_usage_limits(
    db: DbSession,
    authorization: CurrentAuthorizationContext,
    current_user: CurrentActiveUser,
) -> UsageLimitListResponse:
    """List usage-limit policies inside the selected exact boundary."""

    authorization.require("metrics.read")
    organization_id = _selected_boundary_org(authorization)
    policies = await list_usage_limit_policies_for_boundary(
        db,
        organization_id=organization_id,
    )
    filtered: list[UsageLimitPolicyPublic] = []
    for policy in policies:
        if policy.solution_id is None:
            filtered.append(policy)
            continue
        solution = await db.get(Solution, policy.solution_id)
        if solution is None:
            continue
        if solution.visibility != VISIBILITY_PRIVATE:
            filtered.append(policy)
            continue
        if (
            await _private_solution_usage_subject_if_admitted(
                db,
                authorization,
                current_user,
                policy.solution_id,
                action=SolutionAction.VIEW,
            )
            is not None
        ):
            filtered.append(policy)
    return UsageLimitListResponse(policies=filtered)


@router.get("/effective/{scope}/{target_id}", response_model=UsageLimitEffectiveResponse)
async def get_effective_usage_limits(
    scope: str,
    target_id: str,
    db: DbSession,
    authorization: CurrentAuthorizationContext,
    current_user: CurrentActiveUser,
) -> UsageLimitEffectiveResponse:
    """Read effective per-run and aggregate usage-limit diagnostics."""

    parsed_scope = _parse_scope(scope)
    parsed_target = _parse_target_id(parsed_scope, target_id)
    subject = await _authorize_effective_read(
        db,
        authorization,
        current_user,
        scope=parsed_scope,
        target_id=parsed_target,
    )
    return await read_effective_usage_limits(
        db,
        subject_scope=parsed_scope,
        subject=subject,
    )


@router.put("/{scope}/{target_id}", response_model=UsageLimitPolicyPublic)
async def upsert_usage_limit(
    scope: str,
    target_id: str,
    payload: UsageLimitPolicyUpsert,
    db: DbSession,
    authorization: CurrentAuthorizationContext,
    current_user: CurrentActiveUser,
) -> UsageLimitPolicyPublic:
    """Create or replace a usage-limit policy."""

    authorization.require("metrics.readwrite")
    parsed_scope = _parse_scope(scope)
    parsed_target = _parse_target_id(parsed_scope, target_id)
    if parsed_scope is UsageLimitScope.SOLUTION and parsed_target is not None:
        solution = await db.get(Solution, parsed_target)
        if solution is not None and solution.visibility == VISIBILITY_PRIVATE:
            subject = await _private_solution_usage_subject_if_admitted(
                db,
                authorization,
                current_user,
                parsed_target,
                action=SolutionAction.MANAGE,
            )
            if subject is None:
                raise HTTPException(status_code=404, detail="Solution not found")
        else:
            subject = await _resolve_policy_subject(
                db,
                authorization,
                scope=parsed_scope,
                target_id=parsed_target,
            )
    else:
        subject = await _resolve_policy_subject(
            db,
            authorization,
            scope=parsed_scope,
            target_id=parsed_target,
        )
    policy = await upsert_usage_limit_policy(
        db,
        scope=parsed_scope,
        subject=subject,
        per_run=UsageCeilings(**payload.per_run.configured()),
        aggregate=UsageCeilings(**payload.aggregate.configured()),
        aggregate_period=UsageLimitPeriod(payload.aggregate_period),
    )
    await emit_audit(
        db,
        "usage_limit_policy.upsert",
        resource_type="usage_limit_policy",
        details={
            "policy_id": policy.id,
            "scope": policy.scope,
            "scope_key": policy.scope_key,
            "per_run": payload.per_run.configured(),
            "aggregate": payload.aggregate.configured(),
            "aggregate_period": payload.aggregate_period,
        },
    )
    return policy


@router.delete("/{scope}/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_usage_limit(
    scope: str,
    target_id: str,
    db: DbSession,
    authorization: CurrentAuthorizationContext,
    current_user: CurrentActiveUser,
) -> None:
    """Delete a usage-limit policy if it exists."""

    authorization.require("metrics.readwrite")
    parsed_scope = _parse_scope(scope)
    parsed_target = _parse_target_id(parsed_scope, target_id)
    if parsed_scope is UsageLimitScope.SOLUTION and parsed_target is not None:
        solution = await db.get(Solution, parsed_target)
        if solution is not None and solution.visibility == VISIBILITY_PRIVATE:
            subject = await _private_solution_usage_subject_if_admitted(
                db,
                authorization,
                current_user,
                parsed_target,
                action=SolutionAction.MANAGE,
            )
            if subject is None:
                raise HTTPException(status_code=404, detail="Solution not found")
        else:
            subject = await _resolve_policy_subject(
                db,
                authorization,
                scope=parsed_scope,
                target_id=parsed_target,
            )
    else:
        subject = await _resolve_policy_subject(
            db,
            authorization,
            scope=parsed_scope,
            target_id=parsed_target,
        )
    deleted = await delete_usage_limit_policy(
        db,
        scope=parsed_scope,
        subject=subject,
    )
    await emit_audit(
        db,
        "usage_limit_policy.delete",
        resource_type="usage_limit_policy",
        outcome="success" if deleted else "failure",
        details={
            "scope": parsed_scope.value,
            "target_id": target_id,
            "deleted": deleted,
        },
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Usage-limit policy not found")
