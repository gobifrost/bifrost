"""Policy Rules Router.

CRUD for named, reusable policy rules plus usages introspection.
Authorization is boundary-aware: every route requires its cataloged
capability, and concrete writes are constrained to the selected resource
boundary instead of a legacy superuser shortcut.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from src.core.auth import Context
from src.models.orm.organizations import Organization
from src.models.contracts.policy_rule import (
    PolicyRuleCreate,
    PolicyRulePublic,
    PolicyRuleUpdate,
    PolicyRuleUsagesFilePolicyItem,
    PolicyRuleUsagesPublic,
    PolicyRuleUsagesTableItem,
)
from src.repositories.policy_rule import PolicyRuleRepository
from src.services.authorization import (
    AuthorizationBoundaryKind,
    CurrentAuthorizationContext,
)
from src.services.operation_catalog import operation_route
from src.services.policy_rule_service import (
    PolicyRuleInUse,
    PolicyRuleNotFoundError,
    PolicyRuleReadOnly,
    PolicyRuleService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/policy-rules", tags=["Policy Rules"])


def _require_policy_rule_operation(
    authorization: CurrentAuthorizationContext,
    operation_id: str,
) -> None:
    authorization.require_operation(operation_id)


async def _resolve_policy_rule_org_scope(
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    organization_id: UUID | None,
    *,
    allow_managed_collection: bool,
) -> tuple[UUID | None, bool]:
    """Resolve the effective organization scope for a policy-rule read."""

    boundary = authorization.selected_boundary
    if boundary.kind is AuthorizationBoundaryKind.PLATFORM:
        if organization_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Select the platform boundary to read global policy rules",
            )
        return None, False

    if boundary.kind is AuthorizationBoundaryKind.ORGANIZATION:
        assert boundary.organization_id is not None
        if organization_id is not None and organization_id != boundary.organization_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The selected authorization boundary does not match this organization",
            )
        return boundary.organization_id, False

    if not allow_managed_collection:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select one organization before reading or mutating policy rules",
        )

    if organization_id is None:
        return None, True

    is_provider = await ctx.db.scalar(
        select(Organization.is_provider).where(Organization.id == organization_id)
    )
    if is_provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )
    if is_provider:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select a non-provider organization",
        )
    return organization_id, False


def _require_policy_rule_exact_boundary(
    authorization: CurrentAuthorizationContext,
    organization_id: UUID | None,
) -> None:
    """Require the selected exact boundary for a concrete policy rule."""

    boundary = authorization.selected_boundary
    if boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select one organization before reading or mutating policy rules",
        )
    authorization.require_resource_boundary(organization_id)


async def _list_policy_rules_for_exact_org(
    ctx: Context,
    *,
    organization_id: UUID | None,
    domain: str | None,
) -> list[PolicyRulePublic]:
    repo = PolicyRuleRepository(
        ctx.db,
        org_id=organization_id,
        bypass_resource_admission=True,
    )
    kwargs: dict[str, object] = {}
    if domain:
        kwargs["domain"] = domain
    rows = await repo.list(**kwargs)
    return [
        PolicyRulePublic.model_validate(row)
        for row in rows
        if row.organization_id == organization_id
    ]


async def _list_policy_rules_for_managed_collection(
    ctx: Context,
    *,
    domain: str | None,
) -> list[PolicyRulePublic]:
    organization_ids = (
        await ctx.db.execute(
            select(Organization.id).where(Organization.is_provider.is_(False))
        )
    ).scalars().all()

    rows_by_id: dict[UUID, PolicyRulePublic] = {}
    for organization_id in organization_ids:
        for row in await _list_policy_rules_for_exact_org(
            ctx,
            organization_id=organization_id,
            domain=domain,
        ):
            rows_by_id[row.id] = row

    return [rows_by_id[rule_id] for rule_id in sorted(rows_by_id, key=str)]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=PolicyRulePublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a named policy rule",
    **operation_route("policy.rules.create"),
)
async def create_policy_rule(
    body: PolicyRuleCreate,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
) -> PolicyRulePublic:
    """Create a policy rule in the caller's selected boundary."""
    _require_policy_rule_operation(authorization, "policy.rules.create")
    _require_policy_rule_exact_boundary(authorization, body.organization_id)
    svc = PolicyRuleService(ctx.db)
    row = await svc.create(body, actor=authorization.requester)
    await ctx.db.commit()
    return PolicyRulePublic.model_validate(row)


@router.get(
    "",
    response_model=list[PolicyRulePublic],
    summary="List policy rules",
    **operation_route("policy.rules.list"),
)
async def list_policy_rules(
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    domain: str | None = Query(default=None, description="Filter by domain ('file' or 'table')"),
    organization_id: UUID | None = Query(default=None, description="Org scope; omit for all."),
) -> list[PolicyRulePublic]:
    """List policy rules visible in the selected boundary."""
    _require_policy_rule_operation(authorization, "policy.rules.list")
    org_scope, managed_collection = await _resolve_policy_rule_org_scope(
        ctx,
        authorization,
        organization_id,
        allow_managed_collection=True,
    )
    kwargs: dict[str, object] = {}
    if domain:
        kwargs["domain"] = domain
    if managed_collection:
        return await _list_policy_rules_for_managed_collection(
            ctx,
            domain=domain,
        )
    return await _list_policy_rules_for_exact_org(
        ctx,
        organization_id=org_scope,
        domain=domain,
    )


@router.get(
    "/{domain}/{name}",
    response_model=PolicyRulePublic,
    summary="Get a named policy rule",
    **operation_route("policy.rules.get"),
)
async def get_policy_rule(
    domain: str,
    name: str,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    organization_id: UUID | None = Query(default=None),
) -> PolicyRulePublic:
    """Get one policy rule by domain and name."""
    _require_policy_rule_operation(authorization, "policy.rules.get")
    org_scope, _ = await _resolve_policy_rule_org_scope(
        ctx,
        authorization,
        organization_id,
        allow_managed_collection=False,
    )
    svc = PolicyRuleService(ctx.db)
    try:
        row = await svc.get(name, domain, org_id=org_scope)
    except PolicyRuleNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Policy rule '{name}' not found")
    _require_policy_rule_exact_boundary(authorization, row.organization_id)
    return PolicyRulePublic.model_validate(row)


@router.put(
    "/{domain}/{name}",
    response_model=PolicyRulePublic,
    summary="Update a named policy rule",
    **operation_route("policy.rules.update"),
)
async def update_policy_rule(
    domain: str,
    name: str,
    body: PolicyRuleUpdate,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    organization_id: UUID | None = Query(default=None),
) -> PolicyRulePublic:
    """Update an existing policy rule."""
    _require_policy_rule_operation(authorization, "policy.rules.update")
    org_scope, _ = await _resolve_policy_rule_org_scope(
        ctx,
        authorization,
        organization_id,
        allow_managed_collection=False,
    )
    svc = PolicyRuleService(ctx.db)
    try:
        row = await svc.get(name, domain, org_id=org_scope)
    except PolicyRuleNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Policy rule '{name}' not found")
    _require_policy_rule_exact_boundary(authorization, row.organization_id)
    try:
        row = await svc.update(
            name,
            domain,
            body,
            org_id=org_scope,
            actor=authorization.requester,
        )
    except PolicyRuleReadOnly:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Policy rule '{name}' is read-only (built-in)")
    await ctx.db.commit()
    return PolicyRulePublic.model_validate(row)


@router.delete(
    "/{domain}/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a named policy rule",
    **operation_route("policy.rules.delete"),
)
async def delete_policy_rule(
    domain: str,
    name: str,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    organization_id: UUID | None = Query(default=None),
) -> None:
    """Delete a policy rule. Fails with 409 if the rule is in use or read-only."""
    _require_policy_rule_operation(authorization, "policy.rules.delete")
    org_scope, _ = await _resolve_policy_rule_org_scope(
        ctx,
        authorization,
        organization_id,
        allow_managed_collection=False,
    )
    svc = PolicyRuleService(ctx.db)
    try:
        row = await svc.get(name, domain, org_id=org_scope)
    except PolicyRuleNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Policy rule '{name}' not found")
    _require_policy_rule_exact_boundary(authorization, row.organization_id)
    try:
        await svc.delete(
            name,
            domain,
            org_id=org_scope,
            actor=authorization.requester,
        )
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
    **operation_route("policy.rules.list_usages"),
)
async def get_policy_rule_usages(
    domain: str,
    name: str,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    organization_id: UUID | None = Query(default=None),
) -> PolicyRuleUsagesPublic:
    """Return all file-policies and tables that reference this rule."""
    _require_policy_rule_operation(authorization, "policy.rules.list_usages")
    org_scope, _ = await _resolve_policy_rule_org_scope(
        ctx,
        authorization,
        organization_id,
        allow_managed_collection=False,
    )
    svc = PolicyRuleService(ctx.db)
    try:
        row = await svc.get(name, domain, org_id=org_scope)
    except PolicyRuleNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Policy rule '{name}' not found")
    _require_policy_rule_exact_boundary(authorization, row.organization_id)
    usages = await svc.usages(name, domain, org_id=org_scope)
    return PolicyRuleUsagesPublic(
        file_policies=[PolicyRuleUsagesFilePolicyItem(**fp) for fp in usages.file_policies],
        tables=[PolicyRuleUsagesTableItem(**tb) for tb in usages.tables],
        total=usages.total,
    )
