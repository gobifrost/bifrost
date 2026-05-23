"""CRUD endpoints for Custom Claims (org-scoped).

Custom Claims are query-resolved facts about the calling user (e.g.
``allowed_campus_ids``) that table policies in the same org can reference
as ``{claims: <name>}``. See
``docs/superpowers/specs/2026-05-21-table-policies-custom-claims.md``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.claims.registry import (
    claim_dependency_graph,
    find_cycle,
    referenced_claim_names,
)
from src.core.auth import Context, CurrentSuperuser
from src.core.org_filter import (
    OrgFilterType,
    resolve_org_filter,
    resolve_target_org,
)
from src.models.contracts.claims import (
    ClaimsList,
    CustomClaim as ClaimDTO,
    CustomClaimCreate,
    CustomClaimUpdate,
)
from src.models.orm.custom_claims import CustomClaim as ClaimORM
from src.models.orm.tables import Table

router = APIRouter(prefix="/api/claims", tags=["Claims"])


def _resolve_target_org(ctx: Context, scope: str | None) -> UUID:
    """Resolve the target org for write/scoped operations.

    Custom Claims are always tied to a concrete org — there is no global
    scope. Superusers may target any org via ``?scope=<uuid>``; non-superusers
    are forced to their home org and ``scope`` is ignored.
    """
    try:
        target = resolve_target_org(ctx.user, scope, ctx.org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Custom Claims must target a specific organization (set scope=<org_uuid>)",
        )
    return target


async def _check_source_table_exists(
    db: AsyncSession, org_id: UUID, table_name: str
) -> None:
    result = await db.execute(
        select(Table.id).where(
            Table.organization_id == org_id, Table.name == table_name
        )
    )
    if result.first() is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"source table {table_name!r} not found in this org",
        )


async def _check_known_claim_refs(
    db: AsyncSession, org_id: UUID, where: object | None, *, exclude_name: str | None = None
) -> None:
    """422 if ``where`` references claim names that don't exist in this org.

    Equivalent to the policy-save check on tables, but for a claim's own
    ``query.where``. A self-reference (``exclude_name``) is permitted —
    that's a cycle, caught separately by ``_check_no_cycles``.
    """
    refs = referenced_claim_names(where)
    if exclude_name is not None:
        refs.discard(exclude_name)
    if not refs:
        return
    rows = (
        await db.execute(
            select(ClaimORM.name).where(
                ClaimORM.organization_id == org_id, ClaimORM.name.in_(refs)
            )
        )
    ).scalars().all()
    missing = refs - set(rows)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "claim query references unknown claim names",
                "unknown": sorted(missing),
            },
        )


async def _check_no_cycles(db: AsyncSession, org_id: UUID) -> None:
    """Run cycle detection over the full set of claims in the org."""
    # registry.load_org_claims is sync (uses db.execute().scalars()); inline
    # the async equivalent here to avoid a sync/async split in shared/.
    rows = (
        await db.execute(
            select(ClaimORM).where(ClaimORM.organization_id == org_id)
        )
    ).scalars().all()
    claims = {r.name: ClaimDTO.model_validate(r) for r in rows}
    cycle = find_cycle(claim_dependency_graph(claims.values()))
    if cycle is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "claim dependency cycle detected", "cycle": cycle},
        )


async def _tables_referencing_claim(
    db: AsyncSession, org_id: UUID, claim_name: str
) -> list[str]:
    """Return names of tables whose policies reference ``claim_name``."""
    rows = (
        await db.execute(
            select(Table).where(
                Table.organization_id == org_id, Table.access.is_not(None)
            )
        )
    ).scalars().all()
    out: list[str] = []
    for t in rows:
        access = t.access or {}
        for policy in access.get("policies", []):
            if claim_name in referenced_claim_names(policy.get("when")):
                out.append(t.name)
                break
    return out


@router.get("", response_model=ClaimsList, summary="List custom claims")
async def list_claims(
    ctx: Context,
    user: CurrentSuperuser,
    scope: str | None = Query(
        default=None,
        description="Filter scope: omit to list across all orgs (superuser default), or pass an org UUID.",
    ),
) -> ClaimsList:
    """List custom claims.

    Platform admins see claims across every org by default — the
    organization column lets them filter in the UI. Non-superusers don't
    reach this endpoint (gated by ``CurrentSuperuser``).
    """
    try:
        filter_type, filter_org = resolve_org_filter(ctx.user, scope)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    stmt = select(ClaimORM).order_by(ClaimORM.organization_id, ClaimORM.name)
    if filter_type == OrgFilterType.GLOBAL_ONLY:
        # Claims are always org-scoped — no rows match "global only".
        return ClaimsList(claims=[])
    if filter_type in (OrgFilterType.ORG_ONLY, OrgFilterType.ORG_PLUS_GLOBAL):
        stmt = stmt.where(ClaimORM.organization_id == filter_org)
    rows = (await ctx.db.execute(stmt)).scalars().all()
    return ClaimsList(claims=[ClaimDTO.model_validate(r) for r in rows])


@router.get("/{name}", response_model=ClaimDTO, summary="Get a custom claim by name")
async def get_claim(
    name: str,
    ctx: Context,
    user: CurrentSuperuser,
    scope: str | None = Query(
        default=None,
        description="Target organization scope (org UUID). Defaults to caller's home org.",
    ),
) -> ClaimDTO:
    org_id = _resolve_target_org(ctx, scope)
    row = (
        await ctx.db.execute(
            select(ClaimORM).where(
                ClaimORM.organization_id == org_id, ClaimORM.name == name
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="claim not found")
    return ClaimDTO.model_validate(row)


@router.post(
    "",
    response_model=ClaimDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Create a custom claim (admin only)",
)
async def create_claim(
    body: CustomClaimCreate,
    ctx: Context,
    user: CurrentSuperuser,
    scope: str | None = Query(
        default=None,
        description="Target organization scope (org UUID). Defaults to caller's home org.",
    ),
) -> ClaimDTO:
    org_id = _resolve_target_org(ctx, scope)
    await _check_source_table_exists(ctx.db, org_id, body.query.table)
    await _check_known_claim_refs(
        ctx.db, org_id, body.query.where, exclude_name=body.name
    )
    row = ClaimORM(
        organization_id=org_id,
        name=body.name,
        description=body.description,
        type=body.type,
        query=body.query.model_dump(mode="json"),
    )
    ctx.db.add(row)
    try:
        await ctx.db.flush()
    except IntegrityError as exc:
        await ctx.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"claim {body.name!r} already exists in this org",
        ) from exc
    # Cycle check sees the new row via the same session (post-flush).
    try:
        await _check_no_cycles(ctx.db, org_id)
    except HTTPException:
        await ctx.db.rollback()
        raise
    await ctx.db.commit()
    await ctx.db.refresh(row)
    return ClaimDTO.model_validate(row)


@router.patch(
    "/{name}",
    response_model=ClaimDTO,
    summary="Update a custom claim (admin only)",
)
async def update_claim(
    name: str,
    body: CustomClaimUpdate,
    ctx: Context,
    user: CurrentSuperuser,
    scope: str | None = Query(
        default=None,
        description="Target organization scope (org UUID). Defaults to caller's home org.",
    ),
) -> ClaimDTO:
    org_id = _resolve_target_org(ctx, scope)
    row = (
        await ctx.db.execute(
            select(ClaimORM).where(
                ClaimORM.organization_id == org_id, ClaimORM.name == name
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="claim not found")

    fields = body.model_fields_set
    if "description" in fields:
        row.description = body.description
    if "type" in fields and body.type is not None:
        row.type = body.type
    if "query" in fields and body.query is not None:
        await _check_source_table_exists(ctx.db, org_id, body.query.table)
        await _check_known_claim_refs(
            ctx.db, org_id, body.query.where, exclude_name=name
        )
        row.query = body.query.model_dump(mode="json")

    await ctx.db.flush()
    try:
        await _check_no_cycles(ctx.db, org_id)
    except HTTPException:
        await ctx.db.rollback()
        raise
    await ctx.db.commit()
    await ctx.db.refresh(row)
    return ClaimDTO.model_validate(row)


@router.delete(
    "/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a custom claim (admin only)",
)
async def delete_claim(
    name: str,
    ctx: Context,
    user: CurrentSuperuser,
    scope: str | None = Query(
        default=None,
        description="Target organization scope (org UUID). Defaults to caller's home org.",
    ),
) -> None:
    org_id = _resolve_target_org(ctx, scope)
    row = (
        await ctx.db.execute(
            select(ClaimORM).where(
                ClaimORM.organization_id == org_id, ClaimORM.name == name
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="claim not found")

    refs = await _tables_referencing_claim(ctx.db, org_id, name)
    if refs:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "claim is referenced by table policies; remove references first",
                "tables": refs,
            },
        )
    await ctx.db.delete(row)
    await ctx.db.commit()
