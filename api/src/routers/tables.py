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

from src.core.auth import Context, CurrentSuperuser
from src.core.log_safety import log_safe
from src.core.org_filter import OrgFilterType, resolve_org_filter, resolve_target_org
from src.models.contracts.tables import (
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
from src.models.orm.users import UserRole as UserRoleORM
from src.repositories.org_scoped import OrgScopedRepository
from shared.table_access import Action, Caller, check_table_access

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tables", tags=["Tables"])


async def _load_caller(ctx: Context) -> Caller:
    """Build a Caller from the request context."""
    role_q = select(UserRoleORM.role_id).where(UserRoleORM.user_id == ctx.user.user_id)
    role_ids = {r for r in (await ctx.db.execute(role_q)).scalars().all()}
    return Caller(
        user_id=ctx.user.user_id,
        role_ids=frozenset(role_ids),
        is_admin=ctx.user.is_superuser,
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
        """Create a new table."""
        # Check if table already exists in this scope
        existing = await self.get_by_name_strict(data.name)
        if existing:
            raise ValueError(f"Table '{data.name}' already exists")

        table = Table(
            name=data.name,
            description=data.description,
            schema=data.schema,
            organization_id=self.org_id,
            created_by=created_by,
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
        self._creator_filter: str | None = None

    def with_creator_filter(self, user_id: str) -> "DocumentRepository":
        """Return a clone that filters all reads by created_by = user_id."""
        clone = DocumentRepository(self.session, self.table)
        clone._creator_filter = user_id
        return clone

    async def insert(self, data: dict[str, Any], created_by: str | None) -> Document:
        """Insert a new document."""
        doc = Document(
            table_id=self.table.id,
            data=data,
            created_by=created_by,
        )
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

    async def query(self, query_params: DocumentQuery) -> tuple[list[Document], int]:
        """Query documents with filtering and pagination."""
        base_query = select(Document).where(Document.table_id == self.table.id)

        if self._creator_filter is not None:
            base_query = base_query.where(Document.created_by == self._creator_filter)

        # Apply where filters using JSON-native operators
        if query_params.where:
            base_query = _build_document_filters(base_query, query_params.where)

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

    async def count(self, where: dict[str, Any] | None = None) -> int:
        """Count documents matching filter."""
        base_query = select(Document).where(Document.table_id == self.table.id)

        if self._creator_filter is not None:
            base_query = base_query.where(Document.created_by == self._creator_filter)

        if where:
            base_query = _build_document_filters(base_query, where)

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
    table_id: UUID,
    body: DocumentCreate,
    ctx: Context,
) -> DocumentPublic:
    """Insert a new document into the table."""
    table = await _get_table_or_404(ctx, table_id)
    caller = await _load_caller(ctx)
    res = check_table_access(action=Action.CREATE, access=table.access, caller=caller)
    if not res.allow:
        raise HTTPException(status_code=403, detail="Access denied")

    repo = DocumentRepository(ctx.db, table)
    doc = await repo.insert(body.data, created_by=str(ctx.user.user_id))
    await ctx.db.commit()
    # TODO Task 7: publish_document_change(table_id, "insert", doc)
    return DocumentPublic.model_validate(doc)


@router.get(
    "/{table_id}/documents/{doc_id}",
    response_model=DocumentPublic,
    summary="Get a document",
)
async def get_document(
    table_id: UUID,
    doc_id: str,
    ctx: Context,
) -> DocumentPublic:
    """Get a document by ID."""
    table = await _get_table_or_404(ctx, table_id)
    caller = await _load_caller(ctx)
    repo = DocumentRepository(ctx.db, table)
    doc = await repo.get(doc_id)
    if doc is None:
        # 403 instead of 404 to avoid leaking presence to unauthorized callers.
        raise HTTPException(status_code=403, detail="Access denied")
    row_creator = UUID(doc.created_by) if doc.created_by else None
    res = check_table_access(
        action=Action.READ, access=table.access, caller=caller, row_created_by=row_creator
    )
    if not res.allow:
        raise HTTPException(status_code=403, detail="Access denied")
    return DocumentPublic.model_validate(doc)


@router.patch(
    "/{table_id}/documents/{doc_id}",
    response_model=DocumentPublic,
    summary="Update a document",
)
async def update_document(
    table_id: UUID,
    doc_id: str,
    body: DocumentUpdate,
    ctx: Context,
) -> DocumentPublic:
    """Update a document (partial update, merges with existing)."""
    table = await _get_table_or_404(ctx, table_id)
    caller = await _load_caller(ctx)
    repo = DocumentRepository(ctx.db, table)
    existing = await repo.get(doc_id)
    if existing is None:
        raise HTTPException(status_code=403, detail="Access denied")
    row_creator = UUID(existing.created_by) if existing.created_by else None
    res = check_table_access(
        action=Action.UPDATE, access=table.access, caller=caller, row_created_by=row_creator
    )
    if not res.allow:
        raise HTTPException(status_code=403, detail="Access denied")
    doc = await repo.update(doc_id, body.data, updated_by=str(ctx.user.user_id))
    await ctx.db.commit()
    # TODO Task 7: publish_document_change(table_id, "update", doc)
    assert doc is not None
    return DocumentPublic.model_validate(doc)


@router.delete(
    "/{table_id}/documents/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document",
)
async def delete_document(
    table_id: UUID,
    doc_id: str,
    ctx: Context,
) -> None:
    """Delete a document."""
    table = await _get_table_or_404(ctx, table_id)
    caller = await _load_caller(ctx)
    repo = DocumentRepository(ctx.db, table)
    existing = await repo.get(doc_id)
    if existing is None:
        raise HTTPException(status_code=403, detail="Access denied")
    row_creator = UUID(existing.created_by) if existing.created_by else None
    res = check_table_access(
        action=Action.DELETE, access=table.access, caller=caller, row_created_by=row_creator
    )
    if not res.allow:
        raise HTTPException(status_code=403, detail="Access denied")
    await repo.delete(doc_id)
    await ctx.db.commit()
    # TODO Task 7: publish_document_change(table_id, "delete", existing)


@router.post(
    "/{table_id}/documents/query",
    response_model=DocumentListResponse,
    summary="Query documents",
)
async def query_documents(
    table_id: UUID,
    query_params: DocumentQuery,
    ctx: Context,
) -> DocumentListResponse:
    """Query documents with filtering and pagination.

    Returns 404 if the table doesn't exist.
    """
    table = await _get_table_or_404(ctx, table_id)
    caller = await _load_caller(ctx)
    res = check_table_access(action=Action.READ, access=table.access, caller=caller)
    if not res.allow:
        raise HTTPException(status_code=403, detail="Access denied")

    repo = DocumentRepository(ctx.db, table)
    if res.creator_filter_required:
        repo = repo.with_creator_filter(str(caller.user_id))
    documents, total = await repo.query(query_params)
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
    table_id: UUID,
    ctx: Context,
) -> DocumentCountResponse:
    """Count documents in a table.

    Returns 404 if the table doesn't exist.
    """
    table = await _get_table_or_404(ctx, table_id)
    caller = await _load_caller(ctx)
    res = check_table_access(action=Action.READ, access=table.access, caller=caller)
    if not res.allow:
        raise HTTPException(status_code=403, detail="Access denied")
    repo = DocumentRepository(ctx.db, table)
    if res.creator_filter_required:
        repo = repo.with_creator_filter(str(caller.user_id))
    return DocumentCountResponse(count=await repo.count())
