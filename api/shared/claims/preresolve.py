"""Resolve every claim referenced by table policies before evaluation."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import ValidationError

from shared.claims.registry import referenced_claim_names
from shared.policies.compile import compile_to_sql
from shared.policies.probe import compile_read_filter
from src.models.contracts.claims import CustomClaim
from src.models.contracts.policies import Expr, TablePolicies
from src.models.orm.custom_claims import CustomClaim as CustomClaimORM
from src.models.orm.tables import Document, Table

logger = logging.getLogger(__name__)

_DOCUMENT_COLUMNS = {"id", "table_id", "created_by", "updated_by"}


async def preresolve_for_policies(
    user: Any,
    policies: TablePolicies | None,
    db: AsyncSession,
    org_id: UUID | None,
) -> None:
    """Resolve every claim referenced anywhere in ``policies`` onto user.claims."""
    if org_id is None or policies is None or not policies.policies:
        return

    referenced: set[str] = set()
    for policy in policies.policies:
        if policy.when is not None:
            referenced |= referenced_claim_names(policy.when)

    if not referenced:
        return

    claims = await _load_org_claims(db, org_id)
    resolving: set[str] = set()
    for name in referenced:
        claim = claims.get(name)
        if claim is not None:
            await _resolve_claim(claim, claims, user, db, resolving)


async def _load_org_claims(
    db: AsyncSession,
    org_id: UUID,
) -> dict[str, CustomClaim]:
    rows = (
        await db.execute(
            select(CustomClaimORM).where(CustomClaimORM.organization_id == org_id)
        )
    ).scalars().all()
    return {row.name: CustomClaim.model_validate(row) for row in rows}


async def _resolve_claim(
    claim: CustomClaim,
    claims: dict[str, CustomClaim],
    user: Any,
    db: AsyncSession,
    resolving: set[str],
) -> Any:
    cache = _get_or_init_cache(user)
    if claim.name in cache:
        return cache[claim.name]
    if claim.name in resolving:
        cache[claim.name] = [] if claim.type == "list" else None
        return cache[claim.name]

    resolving.add(claim.name)
    try:
        for dependency in referenced_claim_names(claim.query.where):
            dependency_claim = claims.get(dependency)
            if dependency_claim is not None:
                await _resolve_claim(dependency_claim, claims, user, db, resolving)

        rows = await _run_claim_query(claim, claims, user, db, resolving)
        values = [row.get(claim.query.select) for row in rows]
        result = values if claim.type == "list" else (values[0] if values else None)
        cache[claim.name] = result
        return result
    finally:
        resolving.remove(claim.name)


def _get_or_init_cache(user: Any) -> dict[str, Any]:
    cache = getattr(user, "claims", None)
    if cache is None:
        cache = {}
        setattr(user, "claims", cache)
    return cache


async def _run_claim_query(
    claim: CustomClaim,
    claims: dict[str, CustomClaim],
    user: Any,
    db: AsyncSession,
    resolving: set[str],
) -> list[dict[str, Any]]:
    table_name = claim.query.table
    org_id = claim.organization_id

    source = (
        await db.execute(
            select(Table).where(Table.organization_id == org_id, Table.name == table_name)
        )
    ).scalar_one_or_none()
    if source is None:
        logger.warning(
            "claim %r references unknown table %r in org %s; returning []",
            claim.name,
            table_name,
            org_id,
        )
        return []

    # Strict refinement of the caller's read access: claims must NEVER expose
    # rows the user couldn't read directly from the source table. We pre-resolve
    # any claims the source table's read policy itself depends on (a cycle
    # back to the in-progress claim resolves to [] via the existing `resolving`
    # set, which compiles to `IN ()` → false → fail-closed).
    source_policies = _load_source_policies(source)
    for dep_name in _read_policy_claim_deps(source_policies):
        dep_claim = claims.get(dep_name)
        if dep_claim is not None:
            await _resolve_claim(dep_claim, claims, user, db, resolving)
    read_filter = compile_read_filter(source_policies, user)
    if read_filter is None:
        # No rule grants read on the source table → claim resolves to [].
        return []

    stmt = select(Document).where(Document.table_id == source.id, read_filter)
    if claim.query.where is not None:
        try:
            expr = (
                claim.query.where
                if isinstance(claim.query.where, Expr)
                else Expr(claim.query.where)
            )
            stmt = stmt.where(compile_to_sql(expr, user))
        except Exception as exc:  # noqa: BLE001 - fail closed on bad claim queries
            logger.warning(
                "claim %r WHERE failed to compile (%s); returning []",
                claim.name,
                exc,
            )
            return []

    select_key = claim.query.select
    rows = (await db.execute(stmt)).scalars().all()
    return [{select_key: _extract(row, select_key)} for row in rows]


def _load_source_policies(source: Table) -> TablePolicies:
    """Mirror tables._load_policies — fail-closed on malformed JSONB."""
    if not source.access:
        return TablePolicies()
    try:
        return TablePolicies.model_validate(source.access)
    except ValidationError as exc:
        logger.warning(
            "malformed policies on source table %s; defaulting to deny: %s",
            source.id,
            exc,
        )
        return TablePolicies()


def _read_policy_claim_deps(policies: TablePolicies) -> set[str]:
    deps: set[str] = set()
    for policy in policies.policies:
        if "read" not in policy.actions or policy.when is None:
            continue
        deps |= referenced_claim_names(policy.when)
    return deps


def _extract(row: Document, select_key: str) -> Any:
    if select_key in _DOCUMENT_COLUMNS:
        return getattr(row, select_key)

    cursor: Any = row.data or {}
    for part in select_key.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
        if cursor is None:
            return None
    return cursor
