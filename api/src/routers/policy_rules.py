"""
Policy Rules Router

CRUD for named, reusable policy rules + usages introspection.
All endpoints are admin-gated (CurrentSuperuser / platform-admin-or-provider-org bypass).
"""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from src.core.auth import Context, CurrentActiveUser
from src.models.contracts.policy_rule import (
    PolicyRuleCreate,
    PolicyRulePublic,
    PolicyRuleUpdate,
    PolicyRuleUsagesFilePolicyItem,
    PolicyRuleUsagesPublic,
    PolicyRuleUsagesTableItem,
)
from src.repositories.policy_rule import PolicyRuleRepository
from src.services.policy_rule_service import (
    PolicyRuleInUse,
    PolicyRuleNotFoundError,
    PolicyRuleReadOnly,
    PolicyRuleService,
)
from src.services.operation_catalog import operation_route
from src.services.authorization.enforce import (
    load_caller,
    operation_reach,
    org_target,
    require_operation,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/policy-rules", tags=["Policy Rules"])


async def _policy_target(
    ctx: Context, user: CurrentActiveUser, organization_id: UUID | None, operation: str
) -> UUID | None:
    target = ctx.org_id if organization_id is None else organization_id
    caller = await load_caller(ctx.db, user)
    require_operation(caller, operation, org_target(target))
    return target


async def _authorize_policy_row(
    ctx: Context, user: CurrentActiveUser, row, operation: str
) -> None:
    caller = await load_caller(ctx.db, user)
    require_operation(caller, operation, org_target(row.organization_id))


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=PolicyRulePublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a named policy rule",
**operation_route("policy.rules.create"))
async def create_policy_rule(
    body: PolicyRuleCreate,
    ctx: Context,
    user: CurrentActiveUser,
) -> PolicyRulePublic:
    """Create a new (name, domain) policy rule in the caller's org (or global when no org)."""
    await _policy_target(ctx, user, ctx.org_id, operation="policy.rules.create")
    svc = PolicyRuleService(ctx.db)
    row = await svc.create(body)
    await ctx.db.commit()
    return PolicyRulePublic.model_validate(row)


@router.get(
    "",
    response_model=list[PolicyRulePublic],
    summary="List policy rules",
**operation_route("policy.rules.list"))
async def list_policy_rules(
    ctx: Context,
    user: CurrentActiveUser,
    domain: str | None = Query(default=None, description="Filter by domain ('file' or 'table')"),
    organization_id: UUID | None = Query(default=None, description="Org scope; omit for all."),
) -> list[PolicyRulePublic]:
    """List policy rules visible to the caller's scope."""
    caller = await load_caller(ctx.db, user)
    repo = PolicyRuleRepository(ctx.db, org_id=organization_id, is_superuser=True)
    kwargs: dict[str, object] = {}
    if domain:
        kwargs["domain"] = domain
    reach = operation_reach(caller, "policy.rules.list")
    rows = await repo.list(reach=reach, **kwargs)
    return [PolicyRulePublic.model_validate(r) for r in rows]


@router.get(
    "/{domain}/{name}",
    response_model=PolicyRulePublic,
    summary="Get a named policy rule",
**operation_route("policy.rules.get"))
async def get_policy_rule(
    domain: str,
    name: str,
    ctx: Context,
    user: CurrentActiveUser,
    organization_id: UUID | None = Query(default=None),
) -> PolicyRulePublic:
    """Get a single policy rule by domain and name.

    Solution-managed rules are readable here (writes stay blocked).
    """
    target = ctx.org_id if organization_id is None else organization_id
    svc = PolicyRuleService(ctx.db)
    try:
        row = await svc.get(name, domain, org_id=target)
    except PolicyRuleNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Policy rule '{name}' not found")
    await _authorize_policy_row(ctx, user, row, operation="policy.rules.get")
    return PolicyRulePublic.model_validate(row)


@router.put(
    "/{domain}/{name}",
    response_model=PolicyRulePublic,
    summary="Update a named policy rule",
**operation_route("policy.rules.update"))
async def update_policy_rule(
    domain: str,
    name: str,
    body: PolicyRuleUpdate,
    ctx: Context,
    user: CurrentActiveUser,
    organization_id: UUID | None = Query(default=None),
) -> PolicyRulePublic:
    """Update an existing policy rule."""
    target = ctx.org_id if organization_id is None else organization_id
    svc = PolicyRuleService(ctx.db)
    try:
        existing = await svc.get(name, domain, org_id=target)
        await _authorize_policy_row(ctx, user, existing, operation="policy.rules.update")
        row = await svc.update(name, domain, body, org_id=target)
    except PolicyRuleNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Policy rule '{name}' not found")
    except PolicyRuleReadOnly:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Policy rule '{name}' is read-only (built-in)")
    await ctx.db.commit()
    return PolicyRulePublic.model_validate(row)


@router.delete(
    "/{domain}/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a named policy rule",
**operation_route("policy.rules.delete"))
async def delete_policy_rule(
    domain: str,
    name: str,
    ctx: Context,
    user: CurrentActiveUser,
    organization_id: UUID | None = Query(default=None),
) -> None:
    """Delete a policy rule. Fails with 409 if the rule is in use or read-only."""
    target = ctx.org_id if organization_id is None else organization_id
    svc = PolicyRuleService(ctx.db)
    try:
        existing = await svc.get(name, domain, org_id=target)
        await _authorize_policy_row(ctx, user, existing, operation="policy.rules.delete")
        await svc.delete(name, domain, org_id=target)
    except PolicyRuleNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Policy rule '{name}' not found")
    except PolicyRuleReadOnly:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Policy rule '{name}' is read-only (built-in)")
    except PolicyRuleInUse as exc:
        usages_payload = PolicyRuleUsagesPublic(
            file_policies=[PolicyRuleUsagesFilePolicyItem(**fp) for fp in exc.usages.file_policies],
            tables=[PolicyRuleUsagesTableItem(**tb) for tb in exc.usages.tables],
            total=exc.usages.total,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": f"Policy rule '{name}' is in use and cannot be deleted", "usages": usages_payload.model_dump()},
        ) from exc
    await ctx.db.commit()


# ---------------------------------------------------------------------------
# Usages
# ---------------------------------------------------------------------------


@router.get(
    "/{domain}/{name}/usages",
    response_model=PolicyRuleUsagesPublic,
    summary="Get usages of a named policy rule",
**operation_route("policy.rules.list_usages"))
async def get_policy_rule_usages(
    domain: str,
    name: str,
    ctx: Context,
    user: CurrentActiveUser,
    organization_id: UUID | None = Query(default=None),
) -> PolicyRuleUsagesPublic:
    """Return all file-policies and tables that reference this rule."""
    target = ctx.org_id if organization_id is None else organization_id
    svc = PolicyRuleService(ctx.db)
    try:
        existing = await svc.get(name, domain, org_id=target)
        await _authorize_policy_row(ctx, user, existing, operation="policy.rules.list_usages")
        usages = await svc.usages(name, domain, org_id=target)
    except PolicyRuleNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Policy rule '{name}' not found")
    return PolicyRuleUsagesPublic(
        file_policies=[PolicyRuleUsagesFilePolicyItem(**fp) for fp in usages.file_policies],
        tables=[PolicyRuleUsagesTableItem(**tb) for tb in usages.tables],
        total=usages.total,
    )
