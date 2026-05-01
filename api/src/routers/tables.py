"""
Tables Router

Manage tables and documents for app builder data storage.
Uses OrgScopedRepository for standardized org scoping.

Tables follow the same scoping pattern as configs:
- organization_id = NULL: Global table (platform-wide)
- organization_id = UUID: Organization-scoped table
"""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from shared.policies.probe import (
    compile_read_filter,
    evaluate_action,
    make_seed_admin_bypass,
)
from src.core.auth import Context, CurrentSuperuser, UserPrincipal
from src.core.log_safety import log_safe
from src.core.org_filter import OrgFilterType, resolve_org_filter, resolve_target_org
from src.models.contracts.policies import TablePolicies
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
    TableCreate,
    TableListResponse,
    TablePublic,
    TableUpdate,
)
from src.models.orm.applications import Application
from src.models.orm.tables import Document, Table
from src.repositories.org_scoped import OrgScopedRepository
from src.core.pubsub import publish_document_change, publish_policy_changed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tables", tags=["Tables"])


def _load_policies(table: Table) -> TablePolicies:
    """Load TablePolicies from the table's `access` JSONB column. Empty if null."""
    if not table.access:
        return TablePolicies()
    return TablePolicies.model_validate(table.access)


def _row_from_doc(doc: Document) -> dict[str, Any]:
    """Flatten a Document ORM row into the dict shape the evaluator expects.

    Column-mapped fields (id, created_by, updated_by, created_at, updated_at,
    table_id) are placed at the top level alongside the JSONB `data` keys, so
    `{"row": "any_field"}` resolves consistently for both kinds of references.
    UUIDs are stringified to match what `_resolve_user_field` produces.
    """
    return {
        **(doc.data or {}),
        "id": doc.id,
        "table_id": str(doc.table_id),
        "created_by": doc.created_by,
        "updated_by": doc.updated_by,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
    }


def _check_action_or_403(
    action: str,
    table: Table,
    row: dict[str, Any],
    user: UserPrincipal,
) -> None:
    """Run evaluate_action; raise 403 with a generic message on deny.

    The detail is intentionally generic — denials must not leak policy names.
    """
    policies = _load_policies(table)
    if not evaluate_action(action, policies, row, user):
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
        """Update a table by ID.

        Raises:
            ValueError: If `application_id` is set but the application does not exist
                or does not belong to the table's organization.
        """
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
        if data.application_id is not None:
            await _validate_application_for_table(
                self.session, data.application_id, table.organization_id
            )
            table.application_id = data.application_id
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


async def _validate_application_for_table(
    session: AsyncSession,
    application_id: UUID,
    table_organization_id: UUID | None,
) -> None:
    """Ensure `application_id` exists and is compatible with the table's org scope.

    Rules:
    - Application must exist.
    - A global table (organization_id IS NULL) may only link to a global app.
    - An org-scoped table may only link to apps in the same org or a global app.

    Raises:
        ValueError: If the application is missing or org-mismatched.
    """
    result = await session.execute(
        select(Application).where(Application.id == application_id)
    )
    app = result.scalar_one_or_none()
    if app is None:
        raise ValueError(f"Application '{application_id}' not found")

    if app.organization_id is not None and app.organization_id != table_organization_id:
        raise ValueError(
            f"Application '{application_id}' does not belong to the table's organization"
        )


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
    ) -> Document:
        """Insert a new document."""
        kwargs: dict[str, Any] = {
            "table_id": self.table.id,
            "data": data,
            "created_by": created_by,
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

        # Apply ordering
        if query_params.order_by:
            # Order by JSONB field
            order_expr = Document.data[query_params.order_by].astext
            if query_params.order_dir == "desc":
                order_expr = order_expr.desc()
            base_query = base_query.order_by(order_expr)
        else:
            # Default ordering by created_at
            if query_params.order_dir == "desc":
                base_query = base_query.order_by(Document.created_at.desc())
            else:
                base_query = base_query.order_by(Document.created_at.asc())

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


async def _get_table_or_404(ctx: Context, table_id: UUID) -> Table:
    """Get table by UUID, raise 404 if not found."""
    result = await ctx.db.execute(select(Table).where(Table.id == table_id))
    table = result.scalar_one_or_none()
    if table is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")
    return table


def _resolve_target_org_safe(ctx: Context, scope: str | None) -> UUID | None:
    """Resolve the target organization ID from scope parameter (with auth check)."""
    try:
        return resolve_target_org(ctx.user, scope, ctx.org_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


async def get_table_or_404(
    ctx: Context,
    name_or_id: str,
    scope: str | None = None,
) -> Table:
    """Get table by name or UUID, raise 404 if not found."""
    target_org_id = _resolve_target_org_safe(ctx, scope)
    repo = TableRepository(ctx.db, target_org_id, is_superuser=True)

    # Try UUID lookup first
    table: Table | None = None
    try:
        table_uuid = UUID(name_or_id)
        result = await ctx.db.execute(
            select(Table).where(Table.id == table_uuid)
        )
        table = result.scalar_one_or_none()
    except ValueError:
        # Not a UUID — fall through to name-based lookup
        logger.debug(f"table identifier {name_or_id!r} is not a UUID, falling back to name lookup")

    # Fall back to name lookup
    if not table:
        table = await repo.get_by_name(name_or_id)

    if not table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table '{name_or_id}' not found",
        )

    return table


async def get_or_create_table(
    ctx: Context,
    name_or_id: str,
    scope: str | None = None,
    created_by: str | None = None,
) -> Table:
    """Get table by name or UUID, auto-creating if it doesn't exist.

    This is used by insert/upsert/query operations to enable
    schema-less table usage without explicit table creation.
    """
    target_org_id = _resolve_target_org_safe(ctx, scope)
    repo = TableRepository(ctx.db, target_org_id, is_superuser=True)

    # Try UUID lookup first
    table: Table | None = None
    try:
        table_uuid = UUID(name_or_id)
        result = await ctx.db.execute(
            select(Table).where(Table.id == table_uuid)
        )
        table = result.scalar_one_or_none()
    except ValueError:
        # Not a UUID — fall through to name-based lookup / auto-create
        logger.debug(f"table identifier {name_or_id!r} is not a UUID, falling back to name lookup")

    # Fall back to name lookup
    if not table:
        table = await repo.get_by_name(name_or_id)

    if not table:
        # Auto-create table with minimal defaults (only for name-based access)
        from src.models.contracts.tables import TableCreate

        table_data = TableCreate(name=name_or_id)
        table = await repo.create_table(table_data, created_by=created_by or "system")
        logger.info(f"Auto-created table '{name_or_id}' in org {target_org_id}")

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
) -> DocumentPublic:
    """Insert a new document into the table."""
    table = await get_table_or_404(ctx, table_id)
    repo = DocumentRepository(ctx.db, table)
    created_by = str(ctx.user.user_id)

    if body.upsert and body.id:
        # Upsert: update if exists, otherwise insert. Check `update` against
        # the existing row, `create` against the candidate row.
        existing = await repo.get(body.id)
        if existing is not None:
            old_row = _row_from_doc(existing)
            _check_action_or_403("update", table, old_row, ctx.user)
            doc = await repo.update(body.id, body.data, updated_by=created_by)
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
        "updated_by": created_by,
    }
    _check_action_or_403("create", table, candidate_row, ctx.user)
    doc = await repo.insert(body.data, created_by=created_by, doc_id=body.id)
    await ctx.db.commit()
    await publish_document_change(
        table_id=str(table.id),
        action="insert",
        old_row=None,
        new_row=_row_from_doc(doc),
    )
    return DocumentPublic.model_validate(doc)


@router.get(
    "/{table_id}/documents/{doc_id}",
    response_model=DocumentPublic,
    summary="Get a document",
)
async def get_document(
    table_id: str,
    doc_id: str,
    ctx: Context,
) -> DocumentPublic:
    """Get a document by ID."""
    table = await get_table_or_404(ctx, table_id)
    repo = DocumentRepository(ctx.db, table)
    doc = await repo.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    _check_action_or_403("read", table, _row_from_doc(doc), ctx.user)
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
) -> DocumentPublic:
    """Update a document (partial update, merges with existing)."""
    table = await get_table_or_404(ctx, table_id)
    repo = DocumentRepository(ctx.db, table)
    existing = await repo.get(doc_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Document not found")
    old_row = _row_from_doc(existing)
    _check_action_or_403("update", table, old_row, ctx.user)
    doc = await repo.update(doc_id, body.data, updated_by=str(ctx.user.user_id))
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
) -> None:
    """Delete a document."""
    table = await get_table_or_404(ctx, table_id)
    repo = DocumentRepository(ctx.db, table)
    existing = await repo.get(doc_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Document not found")
    old_row = _row_from_doc(existing)
    _check_action_or_403("delete", table, old_row, ctx.user)
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
) -> DocumentListResponse:
    """Query documents with filtering and pagination.

    Returns 404 if the table doesn't exist.
    """
    table = await get_table_or_404(ctx, table_id)

    policies = _load_policies(table)
    read_filter = compile_read_filter(policies, ctx.user)
    if read_filter is None:
        # No rule grants read → empty result. Don't 403 to avoid leaking
        # the table's existence to unauthorized callers.
        return DocumentListResponse(
            documents=[],
            total=0,
            limit=query_params.limit,
            offset=query_params.offset,
        )

    repo = DocumentRepository(ctx.db, table)
    documents, total = await repo.query(query_params, extra_where=read_filter)
    return DocumentListResponse(
        documents=[DocumentPublic.model_validate(d) for d in documents],
        total=total,
        limit=query_params.limit,
        offset=query_params.offset,
    )


@router.get(
    "/{table_id}/documents/count",
    response_model=DocumentCountResponse,
    summary="Count documents",
)
async def count_documents(
    table_id: str,
    ctx: Context,
) -> DocumentCountResponse:
    """Count documents in a table.

    Returns 404 if the table doesn't exist.
    """
    table = await get_table_or_404(ctx, table_id)

    policies = _load_policies(table)
    read_filter = compile_read_filter(policies, ctx.user)
    if read_filter is None:
        # No rule grants read → count zero. Same existence-leak rationale
        # as `query_documents`.
        return DocumentCountResponse(count=0)

    repo = DocumentRepository(ctx.db, table)
    return DocumentCountResponse(count=await repo.count(extra_where=read_filter))


@router.post(
    "/{table_id}/documents/batch",
    response_model=DocumentBatchCreateResponse,
    summary="Batch insert or upsert documents",
)
async def batch_documents(
    table_id: str,
    body: DocumentBatchCreate,
    ctx: Context,
) -> DocumentBatchCreateResponse:
    """Insert (or upsert) multiple documents in a single request.

    When `upsert=true`, each item with a provided id will be updated if it
    exists, otherwise inserted. Items without an id are always inserted.

    All-or-nothing on policy denials: any denied row aborts the whole batch
    with a 403 listing every denied index.
    """
    table = await get_table_or_404(ctx, table_id)
    repo = DocumentRepository(ctx.db, table)
    created_by = str(ctx.user.user_id)
    policies = _load_policies(table)

    # Pre-flight: check every row up front. Collect ALL denials so the
    # client sees the full denied set in one response.
    denied: list[int] = []
    pre_existing: dict[int, Document] = {}
    for i, item in enumerate(body.documents):
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
            "created_by": created_by,
            "updated_by": created_by,
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

    for i, item in enumerate(body.documents):
        try:
            if i in pre_existing:
                old_row = _row_from_doc(pre_existing[i])
                doc = await repo.update(item.id, item.data, updated_by=created_by)
                if doc is not None:
                    await publish_document_change(
                        table_id=str(table.id),
                        action="update",
                        old_row=old_row,
                        new_row=_row_from_doc(doc),
                    )
                inserted += 1
                continue
            doc = await repo.insert(item.data, created_by=created_by, doc_id=item.id)
            await publish_document_change(
                table_id=str(table.id),
                action="insert",
                old_row=None,
                new_row=_row_from_doc(doc),
            )
            inserted += 1
        except Exception as exc:
            errors.append({"id": item.id, "error": str(exc)})

    await ctx.db.commit()
    return DocumentBatchCreateResponse(inserted=inserted, errors=errors)


@router.post(
    "/{table_id}/documents/batch-delete",
    response_model=DocumentBatchDeleteResponse,
    summary="Batch delete documents by ID",
)
async def batch_delete_documents(
    table_id: str,
    body: DocumentBatchDeleteRequest,
    ctx: Context,
) -> DocumentBatchDeleteResponse:
    """Delete multiple documents by ID.

    Skips IDs that don't exist. All-or-nothing on policy denials: any
    denied row aborts the whole batch with a 403 listing every denied index.
    """
    table = await get_table_or_404(ctx, table_id)
    repo = DocumentRepository(ctx.db, table)
    policies = _load_policies(table)

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

    await ctx.db.commit()
    return DocumentBatchDeleteResponse(deleted=deleted)
