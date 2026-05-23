"""
Tables Router

Manage tables and documents for app builder data storage.
Uses OrgScopedRepository for standardized org scoping.

Tables follow the same scoping pattern as configs:
- organization_id = NULL: Global table (platform-wide)
- organization_id = UUID: Organization-scoped table
"""

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from shared.claims.preresolve import preresolve_for_policies
from shared.claims.registry import referenced_claim_names
from shared.policies.probe import (
    compile_read_filter,
    evaluate_action,
    make_seed_admin_bypass,
)
from src.core.auth import Context, CurrentSuperuser, UserPrincipal
from src.core.constants import SYSTEM_USER_UUID
from src.core.log_safety import log_safe
from src.core.org_filter import OrgFilterType, resolve_org_filter, resolve_target_org
from src.models.contracts.policies import (
    PolicyValidationError,
    PolicyValidationResponse,
    TablePolicies,
)
from src.models.contracts.tables import (
    DocumentBatchCreate,
    DocumentBatchCreateResponse,
    DocumentBatchDeleteRequest,
    DocumentBatchDeleteResponse,
    DocumentCountResponse,
    DocumentCreate,
    DocumentListResponse,
    DocumentPublic,
    DocumentQuery,
    DocumentUpdate,
    DocumentUpsert,
    TableCreate,
    TableListResponse,
    TablePublic,
    TableUpdate,
)
from src.models.orm.custom_claims import CustomClaim as CustomClaimORM
from src.models.orm.tables import Document, Table
from src.repositories.org_scoped import OrgScopedRepository
from src.core.pubsub import publish_document_change, publish_policy_changed
from src.services.audit import emit_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tables", tags=["Tables"])


def _load_policies(table: Table) -> TablePolicies:
    """Load TablePolicies from the table's `access` JSONB column.

    Empty if null. Fails closed (empty → default deny) on validation error,
    with a warning log so corrupt data is visible to operators. Without this,
    one bad JSONB blob would take the whole table offline (HTTP 500); now
    users get a predictable deny instead.
    """
    if not table.access:
        return TablePolicies()
    try:
        return TablePolicies.model_validate(table.access)
    except ValidationError as e:
        logger.warning(
            "malformed policies on table %s; defaulting to empty (deny). "
            "Validation error: %s",
            table.id, e,
        )
        return TablePolicies()


def _resolve_attribution(
    user: UserPrincipal,
    body_created_by: str | None,
    body_updated_by: str | None,
) -> tuple[str, str]:
    """Decide attribution (created_by, updated_by) for a document write.

    If the body carries either field, the caller must be the engine
    (SYSTEM_USER_UUID) or a platform admin (is_superuser); otherwise we 403
    so a regular user can't forge attribution.

    Defaulting:
    - both omitted → both default to the caller's id.
    - only created_by provided → updated_by mirrors it (same actor on first write).
    - only updated_by provided → created_by defaults to the caller (only meaningful
      on insert; ignored on the update path).
    """
    has_override = body_created_by is not None or body_updated_by is not None
    if has_override:
        is_engine = user.user_id == SYSTEM_USER_UUID
        if not (is_engine or user.is_superuser):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="created_by/updated_by override requires engine or platform-admin caller",
            )
    caller = str(user.user_id)
    created_by = body_created_by or caller
    updated_by = body_updated_by or body_created_by or caller
    return (created_by, updated_by)


def _row_from_doc(doc: Document) -> dict[str, Any]:
    """Flatten a Document ORM row into the dict shape the evaluator expects.

    Column-mapped fields (id, created_by, updated_by, created_at, updated_at,
    table_id) are placed at the top level alongside the JSONB `data` keys, so
    `{"row": "any_field"}` resolves consistently for both kinds of references.
    UUIDs are stringified to match what `_resolve_user_field` produces, and
    datetimes are ISO-stringified so the same dict round-trips cleanly through
    JSON pubsub / `websocket.send_json` without a custom encoder.
    """
    return {
        **(doc.data or {}),
        "id": doc.id,
        "table_id": str(doc.table_id),
        "created_by": doc.created_by,
        "updated_by": doc.updated_by,
        "created_at": doc.created_at.isoformat() if doc.created_at is not None else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at is not None else None,
    }


async def _check_action_or_403(
    action: str,
    table: Table,
    row: dict[str, Any],
    user: UserPrincipal,
    *,
    db: AsyncSession,
) -> None:
    """Run evaluate_action; raise 403 with a generic message on deny.

    On denial, emits a `policy.deny` audit row before raising so policy
    authors can debug "why can't user X read row Y?" via the audit log.
    The audit record carries actor + table + action metadata only — never
    the row body or policy names (no info leak via audit). The detail
    returned to the caller stays intentionally generic.

    IMPORTANT: callers MUST NOT have uncommitted mutations on `db` when
    calling this — the commit() below would persist them as a side effect
    of the deny. All current call sites either run this before any
    mutation or only after read-only operations.
    """
    policies = _load_policies(table)
    await preresolve_for_policies(
        user,
        policies,
        db,
        table.organization_id,
    )
    if evaluate_action(action, policies, row, user):
        return

    # Resolve the row id only when it's actually a UUID.
    # Document.id is a string primary key (often non-UUID, e.g. email or
    # user-provided id). AuditLog.resource_id is UUID | None — try to
    # coerce, else None.
    raw_id = row.get("id")
    resource_id: UUID | None = None
    if raw_id is not None:
        try:
            resource_id = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))
        except (ValueError, TypeError):
            resource_id = None

    await emit_audit(
        db,
        "policy.deny",
        resource_type="table_document",
        resource_id=resource_id,
        outcome="failure",
        details={
            "policy_action": action,
            "table_id": str(table.id),
            "table_name": table.name,
        },
    )
    # Commit the audit row now — if we let the HTTPException propagate
    # without committing, the request-scoped session rolls back and the
    # audit trail is lost. FOOTGUN: this also commits any uncommitted
    # mutations on `db` from the caller. See docstring — callers must
    # have a clean session at this point.
    await db.commit()
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access denied",
    )


# =============================================================================
# Repository
# =============================================================================


class TableRepository(OrgScopedRepository[Table]):
    """Repository for table operations.

    Tables do NOT have role-based access control - they are SDK/superuser-only
    resources. All endpoints use CurrentSuperuser dependency.
    """

    model = Table
    role_table = None  # Explicit: Tables have NO role-based access control

    async def list_tables(
        self,
        filter_type: OrgFilterType = OrgFilterType.ORG_PLUS_GLOBAL,
    ) -> list[Table]:
        """List tables with specified filter type.

        Supports all OrgFilterType values for superuser flexibility:
        - ALL: No org filter (show everything)
        - GLOBAL_ONLY: Only global records (org_id IS NULL)
        - ORG_ONLY: Only specific org's records (no global)
        - ORG_PLUS_GLOBAL: Cascade scoping (org + global)
        """
        query = select(self.model)

        if filter_type == OrgFilterType.ALL:
            # No organization filter - show all tables
            pass
        elif filter_type == OrgFilterType.GLOBAL_ONLY:
            # Only global records
            query = query.where(self.model.organization_id.is_(None))
        elif filter_type == OrgFilterType.ORG_ONLY:
            # Only specific org, no global fallback
            query = query.where(self.model.organization_id == self.org_id)
        else:
            # ORG_PLUS_GLOBAL: Use cascade scoping
            query = self._apply_cascade_scope(query)

        query = query.order_by(self.model.name)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_name(self, name: str) -> Table | None:
        """Get by name with cascade scoping: org-specific > global.

        Uses cascade lookup to avoid MultipleResultsFound when
        the same name exists in both org scope and global scope.
        """
        return await self.get(name=name)

    async def get_by_name_strict(self, name: str) -> Table | None:
        """Get table by name strictly in current org scope (no fallback)."""
        query = select(self.model).where(
            self.model.name == name,
            self.model.organization_id == self.org_id,
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create_table(
        self,
        data: TableCreate,
        created_by: str,
    ) -> Table:
        """Create a new table.

        When `policies` is omitted from the request, the table is seeded
        with `admin_bypass` so platform admins can still operate on it
        without an explicit rule. Callers who want a strictly-empty policy
        set must pass `policies=TablePolicies()` explicitly.
        """
        # Check if table already exists in this scope
        existing = await self.get_by_name_strict(data.name)
        if existing:
            raise ValueError(f"Table '{data.name}' already exists")

        if data.policies is not None:
            access_json: dict[str, Any] | None = data.policies.model_dump(mode="json")
        else:
            access_json = make_seed_admin_bypass()

        table = Table(
            name=data.name,
            description=data.description,
            schema=data.schema,
            organization_id=self.org_id,
            created_by=created_by,
            access=access_json,
        )
        self.session.add(table)
        await self.session.flush()
        await self.session.refresh(table)

        logger.info(f"Created table '{log_safe(data.name)}' in org {self.org_id}")
        return table

    async def update_table(
        self,
        table_id: UUID,
        data: TableUpdate,
    ) -> Table | None:
        """Update a table by ID."""
        query = select(self.model).where(self.model.id == table_id)
        result = await self.session.execute(query)
        table = result.scalar_one_or_none()
        if not table:
            return None

        if data.name is not None:
            table.name = data.name
        if data.description is not None:
            table.description = data.description
        if data.schema is not None:
            table.schema = data.schema
        if "policies" in data.model_fields_set:
            table.access = (
                data.policies.model_dump(mode="json")
                if data.policies is not None
                else None
            )

        await self.session.flush()
        await self.session.refresh(table)

        logger.info(f"Updated table '{log_safe(table.name)}' (id={log_safe(table_id)})")
        return table

    async def delete_table(self, table_id: UUID) -> bool:
        """Delete a table and all its documents (cascade) by ID."""
        query = select(self.model).where(self.model.id == table_id)
        result = await self.session.execute(query)
        table = result.scalar_one_or_none()
        if not table:
            return False

        await self.session.delete(table)
        await self.session.flush()

        logger.info(f"Deleted table '{log_safe(table.name)}' (id={log_safe(table_id)})")
        return True


def _escape_like(value: str) -> str:
    """Escape LIKE/ILIKE wildcard characters in user input."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_document_filters(base_query: Any, where: dict[str, Any]) -> Any:
    """Build SQLAlchemy filters from where clause with JSON-native operators.

    Supports:
    - Simple equality: {"status": "active"}
    - Comparison operators: {"amount": {"gt": 100, "lte": 1000}}
    - Contains: {"name": {"contains": "acme"}} (case-insensitive substring)
    - Starts/ends with: {"name": {"starts_with": "a"}}
    - IN lists: {"category": {"in": ["a", "b"]}}
    - NULL checks: {"deleted_at": {"is_null": true}}
    - Has key: {"field": {"has_key": true}}
    """
    for field, value in where.items():
        json_field = Document.data[field]

        if isinstance(value, dict):
            # Operator-based filter
            for op, op_value in value.items():
                if op == "eq":
                    if isinstance(op_value, (bool, int, float)):
                        base_query = base_query.where(Document.data.contains({field: op_value}))
                    else:
                        base_query = base_query.where(json_field.astext == str(op_value))
                elif op == "ne":
                    if isinstance(op_value, (bool, int, float)):
                        base_query = base_query.where(~Document.data.contains({field: op_value}))
                    else:
                        base_query = base_query.where(json_field.astext != str(op_value))
                elif op == "contains":
                    # Case-insensitive substring search
                    escaped = _escape_like(str(op_value))
                    base_query = base_query.where(json_field.astext.ilike(f"%{escaped}%"))
                elif op == "starts_with":
                    escaped = _escape_like(str(op_value))
                    base_query = base_query.where(json_field.astext.ilike(f"{escaped}%"))
                elif op == "ends_with":
                    escaped = _escape_like(str(op_value))
                    base_query = base_query.where(json_field.astext.ilike(f"%{escaped}"))
                elif op == "gt":
                    base_query = base_query.where(
                        cast(json_field.astext, String) > str(op_value)
                    )
                elif op == "gte":
                    base_query = base_query.where(
                        cast(json_field.astext, String) >= str(op_value)
                    )
                elif op == "lt":
                    base_query = base_query.where(
                        cast(json_field.astext, String) < str(op_value)
                    )
                elif op == "lte":
                    base_query = base_query.where(
                        cast(json_field.astext, String) <= str(op_value)
                    )
                elif op in ("in", "in_"):
                    if isinstance(op_value, list):
                        def _jsonb_text(v: Any) -> str:
                            if isinstance(v, bool):
                                return str(v).lower()  # True -> "true", False -> "false"
                            return str(v)
                        base_query = base_query.where(
                            json_field.astext.in_([_jsonb_text(v) for v in op_value])
                        )
                elif op == "is_null":
                    if op_value:
                        base_query = base_query.where(json_field.is_(None))
                    else:
                        base_query = base_query.where(json_field.isnot(None))
                elif op == "has_key":
                    if op_value:
                        base_query = base_query.where(Document.data.has_key(field))
                    else:
                        base_query = base_query.where(~Document.data.has_key(field))
        else:
            # Simple equality — use JSONB containment for type-safe comparison
            # This handles booleans, numbers, and strings correctly
            if isinstance(value, (bool, int, float)):
                base_query = base_query.where(Document.data.contains({field: value}))
            else:
                base_query = base_query.where(json_field.astext == str(value))

    return base_query


class DocumentRepository:
    """Repository for document operations within a table."""

    def __init__(self, session: AsyncSession, table: Table):
        self.session = session
        self.table = table

    async def insert(
        self,
        data: dict[str, Any],
        created_by: str | None,
        doc_id: str | None = None,
        updated_by: str | None = None,
    ) -> Document:
        """Insert a new document.

        ``updated_by`` defaults to ``created_by`` so a freshly-inserted row
        carries a non-null updater (matches the row's ``updated_at`` semantics).
        Pass an explicit value to attribute the insert to a different actor
        than the creator (used by the engine when a workflow inserts on
        behalf of a triggering user).
        """
        kwargs: dict[str, Any] = {
            "table_id": self.table.id,
            "data": data,
            "created_by": created_by,
            "updated_by": updated_by if updated_by is not None else created_by,
        }
        if doc_id is not None:
            kwargs["id"] = doc_id
        doc = Document(**kwargs)
        self.session.add(doc)
        await self.session.flush()
        await self.session.refresh(doc)
        return doc

    async def get(self, doc_id: str) -> Document | None:
        """Get document by ID."""
        query = select(Document).where(
            Document.id == doc_id,
            Document.table_id == self.table.id,
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def update(
        self,
        doc_id: str,
        data: dict[str, Any],
        updated_by: str | None,
    ) -> Document | None:
        """Update a document (partial update, merges with existing)."""
        doc = await self.get(doc_id)
        if not doc:
            return None

        # Merge new data with existing
        merged_data = {**doc.data, **data}
        doc.data = merged_data
        doc.updated_by = updated_by

        await self.session.flush()
        await self.session.refresh(doc)
        return doc

    async def upsert(
        self,
        doc_id: str,
        data: dict[str, Any],
        *,
        created_by: str | None,
        updated_by: str | None,
    ) -> tuple[Document, bool]:
        """Atomic upsert by ``(table_id, id)`` — single round trip.

        Returns ``(doc, inserted)`` where ``inserted`` is True if a new row
        was created and False if an existing row was updated.

        Replace semantics on conflict (the JSONB ``data`` column is
        overwritten, not merged). This matches the CLI's prior upsert
        endpoint and lets workflow callers do an idempotent put without a
        round trip to fetch + merge first.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        now = datetime.now(timezone.utc)
        effective_updated_by = updated_by if updated_by is not None else created_by
        stmt = (
            pg_insert(Document)
            .values(
                id=doc_id,
                table_id=self.table.id,
                data=data,
                created_by=created_by,
                updated_by=effective_updated_by,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["table_id", "id"],
                set_={
                    "data": data,
                    "updated_by": effective_updated_by,
                    "updated_at": now,
                },
            )
            .returning(Document.id, (Document.created_at == now).label("inserted"))
        )
        result = await self.session.execute(stmt)
        row = result.one()
        inserted = bool(row.inserted)

        # The upsert ran as raw SQL (bypassing the ORM); any identity-mapped
        # instance for this row from a prior ``get`` carries pre-write attrs.
        # Wipe the identity map so the next ``get`` re-reads from the DB.
        self.session.expunge_all()
        doc = await self.get(doc_id)
        assert doc is not None  # we just upserted it
        return doc, inserted

    async def delete(self, doc_id: str) -> bool:
        """Delete a document."""
        doc = await self.get(doc_id)
        if not doc:
            return False

        await self.session.delete(doc)
        await self.session.flush()
        return True

    async def query(
        self,
        query_params: DocumentQuery,
        *,
        extra_where: ColumnElement | None = None,
    ) -> tuple[list[Document], int]:
        """Query documents with filtering and pagination.

        ``extra_where`` is ANDed into the WHERE clause before pagination —
        used by the REST handlers to push a compiled policy read-filter
        down into the SQL query.
        """
        base_query = select(Document).where(Document.table_id == self.table.id)

        # Apply where filters using JSON-native operators
        if query_params.where:
            base_query = _build_document_filters(base_query, query_params.where)

        if extra_where is not None:
            base_query = base_query.where(extra_where)

        # Get total count before pagination (skip if caller doesn't need it)
        if not query_params.skip_count:
            count_query = base_query.with_only_columns(func.count()).order_by(None)
            count_result = await self.session.execute(count_query)
            total = count_result.scalar() or 0
        else:
            total = -1

        # Apply ordering. Always append `Document.id` as a secondary sort key
        # so OFFSET/LIMIT pagination is stable when the primary key has ties
        # (e.g. rows inserted in the same transaction share `created_at`).
        # Without a tiebreaker, Postgres returns tied rows in arbitrary order
        # and the same id can appear on adjacent pages — or be skipped entirely.
        if query_params.order_by:
            # Order by JSONB field
            order_expr = Document.data[query_params.order_by].astext
            if query_params.order_dir == "desc":
                order_expr = order_expr.desc()
            base_query = base_query.order_by(order_expr, Document.id)
        else:
            # Default ordering by created_at
            if query_params.order_dir == "desc":
                base_query = base_query.order_by(
                    Document.created_at.desc(), Document.id
                )
            else:
                base_query = base_query.order_by(
                    Document.created_at.asc(), Document.id
                )

        # Apply pagination
        base_query = base_query.offset(query_params.offset).limit(query_params.limit)

        result = await self.session.execute(base_query)
        documents = list(result.scalars().all())

        return documents, total

    async def count(
        self,
        where: dict[str, Any] | None = None,
        *,
        extra_where: ColumnElement | None = None,
    ) -> int:
        """Count documents matching filter.

        ``extra_where`` is ANDed in alongside the user-provided filters.
        """
        base_query = select(Document).where(Document.table_id == self.table.id)

        if where:
            base_query = _build_document_filters(base_query, where)

        if extra_where is not None:
            base_query = base_query.where(extra_where)

        count_query = base_query.with_only_columns(func.count()).order_by(None)
        result = await self.session.execute(count_query)
        return result.scalar() or 0


# =============================================================================
# Helper functions
# =============================================================================


def _resolve_target_org_safe(ctx: Context, scope: str | None) -> UUID | None:
    """Resolve the target organization ID from scope parameter (with auth check)."""
    try:
        return resolve_target_org(ctx.user, scope, ctx.org_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


def _validate_policy_claim_refs(
    expr: object,
    known_claim_names: set[str],
) -> None:
    """Reject policy claim references not defined in the table's org."""
    refs = referenced_claim_names(expr)
    missing = refs - known_claim_names
    if missing:
        raise ValueError(
            f"policy references unknown claims: {sorted(missing)}; "
            f"defined in this org: {sorted(known_claim_names)}"
        )


async def _known_claim_names_for_org(
    db: AsyncSession,
    organization_id: UUID | None,
) -> set[str]:
    if organization_id is None:
        return set()
    rows = (
        await db.execute(
            select(CustomClaimORM.name).where(
                CustomClaimORM.organization_id == organization_id
            )
        )
    ).scalars().all()
    return set(rows)


async def _validate_table_policy_claim_refs(
    db: AsyncSession,
    organization_id: UUID | None,
    policies: TablePolicies | None,
) -> None:
    if policies is None:
        return
    known = await _known_claim_names_for_org(db, organization_id)
    for policy in policies.policies:
        _validate_policy_claim_refs(policy.when, known)


async def get_table_or_404(
    ctx: Context,
    name_or_id: str,
    scope: str | None = None,
) -> Table:
    """Get table by name or UUID, raise 404 if not found.

    Routes both UUID and name lookups through ``OrgScopedRepository.get``,
    which already enforces the org gate (its ID-lookup branch returns None
    for non-superusers reaching outside their own-or-global scope). Avoids
    bypassing the gate with raw SELECT.
    """
    target_org_id = _resolve_target_org_safe(ctx, scope)
    repo = TableRepository(
        ctx.db, target_org_id, is_superuser=ctx.user.is_superuser
    )

    # Try UUID lookup first — repo.get(id=...) enforces the org gate for
    # non-superusers (returns None if entity is in a different org).
    table: Table | None = None
    try:
        table_uuid = UUID(name_or_id)
        table = await repo.get(id=table_uuid)
    except ValueError:
        # Not a UUID — fall through to name-based lookup
        logger.debug(
            f"table identifier {log_safe(name_or_id)!r} is not a UUID, "
            "falling back to name lookup"
        )

    # Fall back to name lookup (cascade scoping: org-specific then global)
    if not table:
        table = await repo.get_by_name(name_or_id)

    if not table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table '{name_or_id}' not found",
        )

    return table


# =============================================================================
# Table Endpoints
# =============================================================================


@router.post(
    "",
    response_model=TablePublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a table",
)
async def create_table(
    data: TableCreate,
    ctx: Context,
    user: CurrentSuperuser,
    scope: str | None = Query(
        default=None,
        description="Target scope: 'global' or org UUID. Defaults to current org.",
    ),
) -> TablePublic:
    """Create a new table for storing documents (platform admin only)."""
    # Prefer organization_id from request body; fall back to scope query param (legacy)
    if "organization_id" in (data.model_fields_set or set()):
        target_org_id = data.organization_id
    elif scope is not None:
        target_org_id = _resolve_target_org_safe(ctx, scope)
    else:
        target_org_id = ctx.org_id
    try:
        await _validate_table_policy_claim_refs(ctx.db, target_org_id, data.policies)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    repo = TableRepository(ctx.db, target_org_id, is_superuser=True)
    try:
        table = await repo.create_table(data, created_by=user.email)
        return TablePublic.model_validate(table)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )


@router.get(
    "",
    response_model=TableListResponse,
    summary="List tables",
)
async def list_tables(
    ctx: Context,
    user: CurrentSuperuser,
    scope: str | None = Query(
        default=None,
        description="Filter scope: 'global' for global only, org UUID for specific org.",
    ),
) -> TableListResponse:
    """List all tables in the current scope (platform admin only)."""
    try:
        filter_type, filter_org = resolve_org_filter(ctx.user, scope)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    repo = TableRepository(ctx.db, filter_org, is_superuser=True)
    tables = await repo.list_tables(filter_type)

    return TableListResponse(
        tables=[TablePublic.model_validate(t) for t in tables],
        total=len(tables),
    )


def _loc_to_path(loc: tuple[Any, ...]) -> str:
    """Convert a Pydantic ``error['loc']`` tuple to the JSONPath-like form
    the AST validator emits (``$.policies[0].when.eq[1]``).

    Integer locs are array indices and attach to the previous segment;
    string locs are dotted-property segments. Returns ``$`` for an empty
    loc (e.g. a top-level type error before any field was reached).
    """
    parts = ["$"]
    for x in loc:
        if isinstance(x, int):
            parts[-1] = parts[-1] + f"[{x}]"
        else:
            parts.append(str(x))
    return ".".join(parts) if len(parts) > 1 else "$"


def _split_value_error_msg(msg: str) -> tuple[str, bool, str]:
    """Split a Pydantic-wrapped Expr ``ValueError`` message back into its
    embedded ``$.<path>: <message>`` parts.

    Pydantic v2 prefixes ``ValueError`` raises from custom validators with
    the literal ``"Value error, "``. The wrapped message itself starts
    with the AST validator's own ``$.<path>: `` prefix (added by
    ``_validate_operand`` so error context survives the call stack).

    Returns ``(inner_path, separator_present, message)``. When the message
    doesn't match the expected shape, returns ``("$", False, msg)`` so the
    caller can fall through and use the loc-derived path verbatim.
    """
    PREFIX = "Value error, "
    body = msg[len(PREFIX):] if msg.startswith(PREFIX) else msg
    if not body.startswith("$"):
        return ("$", False, msg)
    path, sep, rest = body.partition(": ")
    if not sep:
        return ("$", False, msg)
    return (path, True, rest)


@router.post(
    "/policies/validate",
    response_model=PolicyValidationResponse,
    summary="Validate a TablePolicies document without persisting it.",
    description=(
        "Runs the same AST validator the table create/update endpoints use, "
        "returning structured errors. Used by the policy editor for live "
        "feedback. On save, the create/update endpoints validate "
        "authoritatively. Always returns 200 — the validation outcome is in "
        "the body, not the status code."
    ),
)
async def validate_policies(
    user: CurrentSuperuser,
    body: Any = Body(...),
) -> PolicyValidationResponse:
    """Validate a candidate ``TablePolicies`` payload.

    The body is typed as ``dict | list`` (rather than ``TablePolicies``) so
    FastAPI doesn't intercept validation errors as 422 before this handler
    runs — we want to capture the full error list and surface it as
    structured ``{path, message}`` entries in the response body. Anything
    that isn't a JSON object (e.g. a list at the root, or a non-object
    primitive) collapses to a single root-level error.

    The validator (``Expr``) raises ``ValueError`` with messages already
    prefixed by their AST path (``$.policies[0].when.eq[1]: ...``); we
    split that prefix back out into the structured ``path``/``message``
    pair. Pydantic's own ``ValidationError`` (e.g. wrong type for
    ``actions``) goes through the standard ``loc``-tuple → path conversion
    via ``_loc_to_path``.

    Auth: matches the rest of the tables router (``CurrentSuperuser``).
    The validator does not touch any tenant data, but tables are
    superuser-only resources so the endpoint should not be reachable to
    non-admin callers either.
    """
    if not isinstance(body, dict):
        return PolicyValidationResponse(
            ok=False,
            errors=[
                PolicyValidationError(
                    path="$",
                    message="root must be an object {policies: [...]}",
                )
            ],
        )

    try:
        TablePolicies.model_validate(body)
        return PolicyValidationResponse(ok=True)
    except ValidationError as e:
        errors: list[PolicyValidationError] = []
        for err in e.errors():
            path = _loc_to_path(err.get("loc", ()))
            msg = err.get("msg", "validation error")
            # ``Expr``'s recursive ``_validate_operand`` raises ``ValueError``
            # with messages already prefixed by their AST path
            # (``$.eq: eq does not accept null literals ...``). Pydantic v2
            # wraps the raise as ``"Value error, <original>"``, so the inner
            # path ends up embedded in ``msg`` instead of in ``loc``. Splice
            # the inner path onto the loc path so the client gets the full
            # ``$.policies[0].when.eq[1]`` form.
            inner_path, sep, inner_msg = _split_value_error_msg(msg)
            if sep:
                path = path + inner_path[1:] if inner_path != "$" else path
                msg = inner_msg
            errors.append(PolicyValidationError(path=path, message=msg))
        return PolicyValidationResponse(ok=False, errors=errors)
    except ValueError as e:
        # The Expr validator's ValueError is already path-prefixed
        # (``$.policies[0].when.eq[1]: <message>``). Split the prefix back
        # out so the client doesn't render the path twice. This branch
        # fires when the ValueError is raised outside Pydantic's own
        # validation context (defensive — the ``Expr`` raises currently
        # surface as the ValidationError branch above).
        text = str(e)
        path, sep, msg = text.partition(": ")
        return PolicyValidationResponse(
            ok=False,
            errors=[
                PolicyValidationError(
                    path=path if sep else "$",
                    message=msg if sep else text,
                )
            ],
        )


@router.get(
    "/{table_id}",
    response_model=TablePublic,
    summary="Get table metadata",
)
async def get_table(
    table_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
) -> TablePublic:
    """Get table metadata by UUID (platform admin only)."""
    result = await ctx.db.execute(select(Table).where(Table.id == table_id))
    table = result.scalar_one_or_none()
    if not table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table '{table_id}' not found",
        )
    return TablePublic.model_validate(table)


@router.patch(
    "/{table_id}",
    response_model=TablePublic,
    summary="Update table",
)
async def update_table(
    table_id: UUID,
    data: TableUpdate,
    ctx: Context,
    user: CurrentSuperuser,
) -> TablePublic:
    """Update table metadata by ID (platform admin only)."""
    if "policies" in data.model_fields_set:
        existing_table = (
            await ctx.db.execute(select(Table).where(Table.id == table_id))
        ).scalar_one_or_none()
        if existing_table is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table '{table_id}' not found",
            )
        try:
            await _validate_table_policy_claim_refs(
                ctx.db,
                existing_table.organization_id,
                data.policies,
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(e),
            )

    repo = TableRepository(ctx.db, ctx.org_id, is_superuser=True)
    try:
        table = await repo.update_table(table_id, data)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    if not table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table '{table_id}' not found",
        )

    if "policies" in data.model_fields_set:
        await publish_policy_changed(str(table.id))

    return TablePublic.model_validate(table)


@router.delete(
    "/{table_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete table",
)
async def delete_table(
    table_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
) -> None:
    """Delete a table and all its documents by ID (platform admin only)."""
    repo = TableRepository(ctx.db, ctx.org_id, is_superuser=True)
    success = await repo.delete_table(table_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table '{table_id}' not found",
        )


# =============================================================================
# Document Endpoints
# =============================================================================


@router.post(
    "/{table_id}/documents",
    response_model=DocumentPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Insert a document",
)
async def insert_document(
    table_id: str,
    body: DocumentCreate,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> DocumentPublic:
    """Insert a new document into the table."""
    table = await get_table_or_404(ctx, table_id, scope=scope)
    repo = DocumentRepository(ctx.db, table)
    created_by, updated_by = _resolve_attribution(
        ctx.user, body.created_by, body.updated_by
    )

    if body.upsert and body.id:
        # Upsert: update if exists, otherwise insert. Check `update` against
        # the existing row, `create` against the candidate row.
        existing = await repo.get(body.id)
        if existing is not None:
            old_row = _row_from_doc(existing)
            await _check_action_or_403("update", table, old_row, ctx.user, db=ctx.db)
            doc = await repo.update(body.id, body.data, updated_by=updated_by)
            if doc is None:
                raise HTTPException(status_code=404, detail="Document not found")
            await ctx.db.commit()
            await publish_document_change(
                table_id=str(table.id),
                action="update",
                old_row=old_row,
                new_row=_row_from_doc(doc),
            )
            return DocumentPublic.model_validate(doc)

    candidate_row: dict[str, Any] = {
        **body.data,
        "id": body.id,
        "created_by": created_by,
        "updated_by": updated_by,
    }
    await _check_action_or_403("create", table, candidate_row, ctx.user, db=ctx.db)
    doc = await repo.insert(
        body.data, created_by=created_by, doc_id=body.id, updated_by=updated_by
    )
    await ctx.db.commit()
    await publish_document_change(
        table_id=str(table.id),
        action="insert",
        old_row=None,
        new_row=_row_from_doc(doc),
    )
    return DocumentPublic.model_validate(doc)


@router.post(
    "/{table_id}/documents/upsert",
    response_model=DocumentPublic,
    summary="Upsert a document by id",
)
async def upsert_document(
    table_id: str,
    body: DocumentUpsert,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> DocumentPublic:
    """Atomically upsert a document by id (single ``INSERT ... ON CONFLICT DO UPDATE``).

    On conflict the JSONB ``data`` column is **replaced**, not merged — use
    PATCH ``/{doc_id}`` for partial updates with merge semantics.

    The candidate row is policy-checked for ``create``; if a row already
    exists, it is also policy-checked for ``update`` against its pre-image.
    Either denial returns 403; the row is not written.

    NOTE: This route is declared BEFORE ``GET /{table_id}/documents/{doc_id}``
    so the literal ``/upsert`` segment matches first. Reversing the order
    binds ``doc_id="upsert"`` and the endpoint becomes unreachable.
    """
    table = await get_table_or_404(ctx, table_id, scope=scope)
    repo = DocumentRepository(ctx.db, table)
    created_by, updated_by = _resolve_attribution(
        ctx.user, body.created_by, body.updated_by
    )

    existing = await repo.get(body.id)
    old_row: dict[str, Any] | None = None
    if existing is not None:
        old_row = _row_from_doc(existing)
        await _check_action_or_403("update", table, old_row, ctx.user, db=ctx.db)
    candidate_row: dict[str, Any] = {
        **body.data,
        "id": body.id,
        "created_by": created_by,
        "updated_by": updated_by,
    }
    await _check_action_or_403("create", table, candidate_row, ctx.user, db=ctx.db)

    doc, inserted = await repo.upsert(
        body.id, body.data, created_by=created_by, updated_by=updated_by
    )
    await ctx.db.commit()
    await publish_document_change(
        table_id=str(table.id),
        action="insert" if inserted else "update",
        old_row=None if inserted else old_row,
        new_row=_row_from_doc(doc),
    )
    return DocumentPublic.model_validate(doc)


@router.get(
    "/{table_id}/documents/count",
    response_model=DocumentCountResponse,
    summary="Count documents",
)
async def count_documents(
    table_id: str,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> DocumentCountResponse:
    """Count documents in a table.

    Returns 404 if the table doesn't exist.

    NOTE: This route is declared BEFORE ``GET /{table_id}/documents/{doc_id}``
    so the literal ``/count`` segment matches first. Reversing the order makes
    FastAPI bind ``doc_id="count"`` and return 404, silently disabling the
    count endpoint.
    """
    table = await get_table_or_404(ctx, table_id, scope=scope)

    policies = _load_policies(table)
    await preresolve_for_policies(
        ctx.user,
        policies,
        ctx.db,
        table.organization_id,
    )
    read_filter = compile_read_filter(policies, ctx.user)
    if read_filter is None:
        # No rule grants read → count zero. Same existence-leak rationale
        # as `query_documents`.
        return DocumentCountResponse(count=0)

    repo = DocumentRepository(ctx.db, table)
    return DocumentCountResponse(count=await repo.count(extra_where=read_filter))


@router.get(
    "/{table_id}/documents/{doc_id}",
    response_model=DocumentPublic,
    summary="Get a document",
)
async def get_document(
    table_id: str,
    doc_id: str,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> DocumentPublic:
    """Get a document by ID."""
    table = await get_table_or_404(ctx, table_id, scope=scope)
    repo = DocumentRepository(ctx.db, table)
    doc = await repo.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    await _check_action_or_403("read", table, _row_from_doc(doc), ctx.user, db=ctx.db)
    return DocumentPublic.model_validate(doc)


@router.patch(
    "/{table_id}/documents/{doc_id}",
    response_model=DocumentPublic,
    summary="Update a document",
)
async def update_document(
    table_id: str,
    doc_id: str,
    body: DocumentUpdate,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> DocumentPublic:
    """Update a document (partial update, merges with existing)."""
    table = await get_table_or_404(ctx, table_id, scope=scope)
    repo = DocumentRepository(ctx.db, table)
    _, updated_by = _resolve_attribution(ctx.user, None, body.updated_by)
    existing = await repo.get(doc_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Document not found")
    old_row = _row_from_doc(existing)
    await _check_action_or_403("update", table, old_row, ctx.user, db=ctx.db)
    doc = await repo.update(doc_id, body.data, updated_by=updated_by)
    if doc is None:
        # Lost a race with a concurrent delete after we fetched + access-checked.
        raise HTTPException(status_code=404, detail="Document not found")
    await ctx.db.commit()
    await publish_document_change(
        table_id=str(table.id),
        action="update",
        old_row=old_row,
        new_row=_row_from_doc(doc),
    )
    return DocumentPublic.model_validate(doc)


@router.delete(
    "/{table_id}/documents/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document",
)
async def delete_document(
    table_id: str,
    doc_id: str,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> None:
    """Delete a document."""
    table = await get_table_or_404(ctx, table_id, scope=scope)
    repo = DocumentRepository(ctx.db, table)
    existing = await repo.get(doc_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Document not found")
    old_row = _row_from_doc(existing)
    await _check_action_or_403("delete", table, old_row, ctx.user, db=ctx.db)
    deleted = await repo.delete(doc_id)
    await ctx.db.commit()
    if deleted:
        await publish_document_change(
            table_id=str(table.id),
            action="delete",
            old_row=old_row,
            new_row=None,
        )


@router.post(
    "/{table_id}/documents/query",
    response_model=DocumentListResponse,
    summary="Query documents",
)
async def query_documents(
    table_id: str,
    query_params: DocumentQuery,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> DocumentListResponse:
    """Query documents with filtering and pagination.

    Returns 404 if the table doesn't exist.
    """
    table = await get_table_or_404(ctx, table_id, scope=scope)

    policies = _load_policies(table)
    await preresolve_for_policies(
        ctx.user,
        policies,
        ctx.db,
        table.organization_id,
    )
    read_filter = compile_read_filter(policies, ctx.user)
    if read_filter is None:
        # No rule grants read → empty result. Don't 403 to avoid leaking
        # the table's existence to unauthorized callers.
        return DocumentListResponse(
            table_id=table.id,
            documents=[],
            total=0,
            limit=query_params.limit,
            offset=query_params.offset,
        )

    repo = DocumentRepository(ctx.db, table)
    documents, total = await repo.query(query_params, extra_where=read_filter)
    return DocumentListResponse(
        table_id=table.id,
        documents=[DocumentPublic.model_validate(d) for d in documents],
        total=total,
        limit=query_params.limit,
        offset=query_params.offset,
    )


@router.post(
    "/{table_id}/documents/batch",
    response_model=DocumentBatchCreateResponse,
    summary="Batch insert or upsert documents",
)
async def batch_documents(
    table_id: str,
    body: DocumentBatchCreate,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> DocumentBatchCreateResponse:
    """Insert (or upsert) multiple documents in a single request.

    When `upsert=true`, each item with a provided id will be updated if it
    exists, otherwise inserted. Items without an id are always inserted.

    All-or-nothing on policy denials: any denied row aborts the whole batch
    with a 403 listing every denied index.
    """
    table = await get_table_or_404(ctx, table_id, scope=scope)
    repo = DocumentRepository(ctx.db, table)
    policies = _load_policies(table)
    await preresolve_for_policies(
        ctx.user,
        policies,
        ctx.db,
        table.organization_id,
    )

    # Pre-resolve attribution per item up front so any forged-attribution
    # 403 surfaces before we do work and applies all-or-nothing across
    # the batch (consistent with the policy-denial semantics below).
    attribution: list[tuple[str, str]] = [
        _resolve_attribution(ctx.user, item.created_by, item.updated_by)
        for item in body.documents
    ]

    # Pre-flight: check every row up front. Collect ALL denials so the
    # client sees the full denied set in one response.
    denied: list[int] = []
    pre_existing: dict[int, Document] = {}
    for i, item in enumerate(body.documents):
        item_created_by, item_updated_by = attribution[i]
        if body.upsert and item.id:
            existing = await repo.get(item.id)
            if existing is not None:
                pre_existing[i] = existing
                if not evaluate_action(
                    "update", policies, _row_from_doc(existing), ctx.user
                ):
                    denied.append(i)
                continue
        candidate_row: dict[str, Any] = {
            **item.data,
            "id": item.id,
            "created_by": item_created_by,
            "updated_by": item_updated_by,
        }
        if not evaluate_action("create", policies, candidate_row, ctx.user):
            denied.append(i)

    if denied:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"denied_row_indices": denied},
        )

    inserted = 0
    errors: list[dict[str, Any]] = []
    written: list[Document] = []

    for i, item in enumerate(body.documents):
        item_created_by, item_updated_by = attribution[i]
        try:
            if i in pre_existing:
                old_row = _row_from_doc(pre_existing[i])
                doc = await repo.update(item.id, item.data, updated_by=item_updated_by)
                if doc is not None:
                    await publish_document_change(
                        table_id=str(table.id),
                        action="update",
                        old_row=old_row,
                        new_row=_row_from_doc(doc),
                    )
                    written.append(doc)
                inserted += 1
                continue
            doc = await repo.insert(
                item.data,
                created_by=item_created_by,
                doc_id=item.id,
                updated_by=item_updated_by,
            )
            await publish_document_change(
                table_id=str(table.id),
                action="insert",
                old_row=None,
                new_row=_row_from_doc(doc),
            )
            written.append(doc)
            inserted += 1
        except Exception as exc:
            errors.append({"id": item.id, "error": str(exc)})

    await ctx.db.commit()
    return DocumentBatchCreateResponse(
        inserted=inserted,
        errors=errors,
        documents=[DocumentPublic.model_validate(d) for d in written],
    )


@router.post(
    "/{table_id}/documents/batch-delete",
    response_model=DocumentBatchDeleteResponse,
    summary="Batch delete documents by ID",
)
async def batch_delete_documents(
    table_id: str,
    body: DocumentBatchDeleteRequest,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> DocumentBatchDeleteResponse:
    """Delete multiple documents by ID.

    Skips IDs that don't exist. All-or-nothing on policy denials: any
    denied row aborts the whole batch with a 403 listing every denied index.
    """
    table = await get_table_or_404(ctx, table_id, scope=scope)
    repo = DocumentRepository(ctx.db, table)
    policies = _load_policies(table)
    await preresolve_for_policies(
        ctx.user,
        policies,
        ctx.db,
        table.organization_id,
    )

    # Pre-flight: load each existing row and check `delete` against policy.
    denied: list[int] = []
    existing_by_index: dict[int, Document] = {}
    for i, doc_id in enumerate(body.ids):
        existing = await repo.get(doc_id)
        if existing is None:
            # Skipping non-existent rows is the documented behavior; not a
            # denial, just a no-op.
            continue
        existing_by_index[i] = existing
        if not evaluate_action(
            "delete", policies, _row_from_doc(existing), ctx.user
        ):
            denied.append(i)

    if denied:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"denied_row_indices": denied},
        )

    deleted = 0
    deleted_ids: list[str] = []
    for i, doc_id in enumerate(body.ids):
        existing = existing_by_index.get(i)
        if existing is None:
            continue
        old_row = _row_from_doc(existing)
        ok = await repo.delete(doc_id)
        if ok:
            await publish_document_change(
                table_id=str(table.id),
                action="delete",
                old_row=old_row,
                new_row=None,
            )
            deleted += 1
            deleted_ids.append(doc_id)

    await ctx.db.commit()
    return DocumentBatchDeleteResponse(deleted=deleted, deleted_ids=deleted_ids)
