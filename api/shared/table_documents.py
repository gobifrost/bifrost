"""Shared table document read service.

HTTP handlers in ``src.routers.tables`` and the future engine SDK local
dispatcher call the same functions here so read status, DTO, policy, and
pagination behavior stay identical across transports.

Table resolution (``get_table_or_404``) and Solution/app security gates stay
router-owned — callers resolve the :class:`Table` first, then call into this
module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import String, bindparam, cast, false, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from shared.claims.preresolve import preresolve_for_policies
from shared.policies.probe import compile_read_filter, evaluate_action
from shared.table_batch_writes import _row_from_doc
from src.core.principal import UserPrincipal
from src.models.contracts.tables import (
    DocumentCountResponse,
    DocumentListResponse,
    DocumentPublic,
    DocumentQuery,
)
from src.models.orm.tables import Document, Table
from src.services.audit import emit_table_policy_deny
from src.services.table_policy_loader import load_resolved_table_policies


async def check_table_action_or_403(
    action: str,
    table: Table,
    row: dict[str, Any],
    user: UserPrincipal,
    *,
    db: AsyncSession,
) -> None:
    """Run evaluate_action; raise 403 with a generic message on deny.

    On denial, emits a ``policy.deny`` audit row before raising so policy
    authors can debug "why can't user X read row Y?" via the audit log.
    The audit record carries actor + table + action metadata only — never
    the row body or policy names (no info leak via audit). The detail
    returned to the caller stays intentionally generic.

    IMPORTANT: callers MUST NOT have uncommitted mutations on ``db`` when
    calling this — the commit() below would persist them as a side effect
    of the deny. All current call sites either run this before any
    mutation or only after read-only operations.
    """
    policies = await load_resolved_table_policies(table, db)
    await preresolve_for_policies(
        user,
        policies,
        db,
        table.organization_id,
        table.solution_id,
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

    await emit_table_policy_deny(
        db,
        policy_action=action,
        table_id=table.id,
        table_name=table.name,
        resource_id=resource_id,
    )
    # Commit the audit row now — if we let the HTTPException propagate
    # without committing, the request-scoped session rolls back and the
    # audit trail is lost. FOOTGUN: this also commits any uncommitted
    # mutations on ``db`` from the caller. See docstring — callers must
    # have a clean session at this point.
    await db.commit()
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access denied",
    )


def _escape_like(value: str) -> str:
    """Escape LIKE/ILIKE wildcard characters in user input."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _document_id_prefix_like_pattern(prefix: str) -> str:
    """Build a slash-escaped LIKE pattern for literal document ID prefixes."""
    return (
        prefix.replace("/", "//")
        .replace("%", "/%")
        .replace("_", "/_")
        + "%"
    )


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

        if query_params.document_ids is not None:
            unique_document_ids = list(dict.fromkeys(query_params.document_ids))
            base_query = base_query.where(
                Document.id.in_(unique_document_ids)
                if unique_document_ids
                else false()
            )

        document_id_pagination = (
            query_params.after_document_id is not None
            or query_params.document_id_prefix is not None
        )
        document_id_order_expr = Document.id
        if query_params.document_id_prefix is not None:
            prefix = query_params.document_id_prefix
            document_id_order_expr = literal_column(
                'documents.id COLLATE "C"',
                type_=String(),
            )
            base_query = base_query.where(document_id_order_expr >= prefix)
            base_query = base_query.where(
                document_id_order_expr.like(
                    bindparam(
                        "document_id_prefix_pattern",
                        value=_document_id_prefix_like_pattern(prefix),
                        type_=String(),
                        literal_execute=True,
                    ),
                    escape="/",
                )
            )
        if query_params.after_document_id is not None:
            base_query = base_query.where(
                document_id_order_expr > query_params.after_document_id
            )

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
        if document_id_pagination:
            base_query = base_query.order_by(document_id_order_expr)
        elif query_params.order_by:
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


async def get_table_document(
    db: AsyncSession,
    table: Table,
    doc_id: str,
    user: UserPrincipal,
) -> DocumentPublic:
    """Fetch a single document, enforcing the ``read`` policy.

    Returns 404 for a missing row; denies with 403 (plus the shared deny
    audit + commit) when the read policy rejects the row.
    """
    repo = DocumentRepository(db, table)
    doc = await repo.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    await check_table_action_or_403("read", table, _row_from_doc(doc), user, db=db)
    return DocumentPublic.model_validate(doc)


async def query_table_documents(
    db: AsyncSession,
    table: Table,
    query_params: DocumentQuery,
    user: UserPrincipal,
) -> DocumentListResponse:
    """Query documents with policy-scoped read filtering.

    Returns an empty result when no read policy grants access (avoids
    leaking the table's existence to unauthorized callers).
    """
    policies = await load_resolved_table_policies(table, db)
    await preresolve_for_policies(
        user,
        policies,
        db,
        table.organization_id,
        table.solution_id,
    )
    read_filter = compile_read_filter(policies, user)
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

    repo = DocumentRepository(db, table)
    documents, total = await repo.query(query_params, extra_where=read_filter)
    return DocumentListResponse(
        table_id=table.id,
        documents=[DocumentPublic.model_validate(d) for d in documents],
        total=total,
        limit=query_params.limit,
        offset=query_params.offset,
    )


async def count_table_documents(
    db: AsyncSession,
    table: Table,
    user: UserPrincipal,
    where: dict[str, Any] | None = None,
) -> DocumentCountResponse:
    """Count documents with policy-scoped read filtering.

    Returns zero when no read policy grants access. Same existence-leak
    rationale as :func:`query_table_documents`.
    """
    policies = await load_resolved_table_policies(table, db)
    await preresolve_for_policies(
        user,
        policies,
        db,
        table.organization_id,
        table.solution_id,
    )
    read_filter = compile_read_filter(policies, user)
    if read_filter is None:
        # No rule grants read → count zero. Same existence-leak rationale
        # as `query_table_documents`.
        return DocumentCountResponse(count=0)

    repo = DocumentRepository(db, table)
    return DocumentCountResponse(count=await repo.count(where, extra_where=read_filter))
