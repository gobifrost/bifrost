"""
Policy Rules Router

CRUD for named, reusable policy rules + usages introspection.
All endpoints are admin-gated (CurrentSuperuser / platform-admin-or-provider-org bypass).
"""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from src.core.auth import Context, CurrentSuperuser
from src.models.contracts.policy_rule import (
    PolicyRuleCreate,
    PolicyRulePublic,
    PolicyRuleUpdate,
)
from src.repositories.policy_rule import PolicyRuleRepository
from src.services.policy_rule_service import (
    PolicyRuleInUse,
    PolicyRuleNotFoundError,
    PolicyRuleReadOnly,
    PolicyRuleService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/policy-rules", tags=["Policy Rules"])


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=PolicyRulePublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a named policy rule",
)
async def create_policy_rule(
    body: PolicyRuleCreate,
    ctx: Context,
    user: CurrentSuperuser,
) -> PolicyRulePublic:
    """Create a new (name, domain) policy rule in the caller's org (or global when no org)."""
    svc = PolicyRuleService(ctx.db)
    row = await svc.create(body, actor=user)
    await ctx.db.commit()
    return PolicyRulePublic.model_validate(row)


@router.get(
    "",
    response_model=list[PolicyRulePublic],
    summary="List policy rules",
)
async def list_policy_rules(
    ctx: Context,
    user: CurrentSuperuser,
    domain: str | None = Query(default=None, description="Filter by domain ('file' or 'table')"),
    organization_id: UUID | None = Query(default=None, description="Org scope; omit for all."),
) -> list[PolicyRulePublic]:
    """List policy rules visible to the caller's scope."""
    repo = PolicyRuleRepository(ctx.db, org_id=organization_id, is_superuser=True)
    kwargs: dict[str, object] = {}
    if domain:
        kwargs["domain"] = domain
    rows = await repo.list(**kwargs)
    return [PolicyRulePublic.model_validate(r) for r in rows]


@router.put(
    "/{domain}/{name}",
    response_model=PolicyRulePublic,
    summary="Update a named policy rule",
)
async def update_policy_rule(
    domain: str,
    name: str,
    body: PolicyRuleUpdate,
    ctx: Context,
    user: CurrentSuperuser,
    organization_id: UUID | None = Query(default=None),
) -> PolicyRulePublic:
    """Update an existing policy rule."""
    svc = PolicyRuleService(ctx.db)
    try:
        row = await svc.update(name, domain, body, org_id=organization_id, actor=user)
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
)
async def delete_policy_rule(
    domain: str,
    name: str,
    ctx: Context,
    user: CurrentSuperuser,
    organization_id: UUID | None = Query(default=None),
) -> None:
    """Delete a policy rule. Fails with 409 if the rule is in use or read-only."""
    svc = PolicyRuleService(ctx.db)
    try:
        await svc.delete(name, domain, org_id=organization_id, actor=user)
    except PolicyRuleNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Policy rule '{name}' not found")
    except PolicyRuleReadOnly:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Policy rule '{name}' is read-only (built-in)")
    except PolicyRuleInUse as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Policy rule '{name}' is in use and cannot be deleted",
        ) from exc
    await ctx.db.commit()


# ---------------------------------------------------------------------------
# Usages
# ---------------------------------------------------------------------------


@router.get(
    "/{domain}/{name}/usages",
    summary="Get usages of a named policy rule",
)
async def get_policy_rule_usages(
    domain: str,
    name: str,
    ctx: Context,
    user: CurrentSuperuser,
    organization_id: UUID | None = Query(default=None),
) -> dict:
    """Return all file-policies and tables that reference this rule."""
    svc = PolicyRuleService(ctx.db)
    try:
        usages = await svc.usages(name, domain, org_id=organization_id)
    except PolicyRuleNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Policy rule '{name}' not found")
    return {
        "total": usages.total,
        "file_policies": usages.file_policies,
        "tables": usages.tables,
    }
