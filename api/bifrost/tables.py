"""
Tables SDK for Bifrost - API-only implementation.

Provides Python API for table and document management (CRUD operations).
All operations go through HTTP API endpoints.
All methods are async and must be awaited.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from .client import get_client, raise_for_status_with_detail
from .models import TableInfo, DocumentData, DocumentList, BatchResult, BatchDeleteResult
from ._context import resolve_scope, _execution_context


def _current_context():
    """Return the active ExecutionContext, or None if not in a workflow execution."""
    return _execution_context.get()


def _scope_query(scope: str | None) -> str:
    """Build the ``?scope=`` querystring fragment for REST table-document URLs.

    Returns ``""`` when scope is None so the URL ends cleanly.
    """
    return f"?{urlencode({'scope': scope})}" if scope else ""


async def _ensure_table_exists(table: str, scope: str | None) -> None:
    """Create the table if it doesn't already exist (auto-create-on-insert).

    This used to live in the CLI handler (``_find_or_create_table_for_sdk``);
    moved here so the SDK and web UI share one document-write code path.
    Idempotent: a 409 from a concurrent creator is treated as success.
    """
    client = get_client()
    response = await client.post(
        f"/api/tables{_scope_query(scope)}",
        json={"name": table},
    )
    if response.status_code == 409:
        return
    raise_for_status_with_detail(response)


class tables:
    """
    Table and document management operations.

    Allows workflows to create tables and store/query documents.
    All operations are performed via HTTP API endpoints.

    All methods are async - await is required.

    Example:
        >>> from bifrost import tables
        >>> # Create a table
        >>> table = await tables.create("customers", table_schema={"type": "object"})
        >>> # Insert a document
        >>> doc = await tables.insert("customers", {"name": "Acme", "email": "info@acme.com"})
        >>> # Query with comparison operators
        >>> results = await tables.query(
        ...     "customers",
        ...     where={
        ...         "status": "active",
        ...         "revenue": {"gte": 10000},
        ...         "name": {"ilike": "%acme%"}
        ...     }
        ... )
        >>> for doc in results.documents:
        ...     print(doc.data)
    """

    # =========================================================================
    # Table Operations
    # =========================================================================

    @staticmethod
    async def create(
        name: str,
        description: str | None = None,
        table_schema: dict[str, Any] | None = None,
        scope: str | None = None,
        app: str | None = None,
    ) -> TableInfo:
        """
        Create a new table.

        Args:
            name: Table name (unique within scope)
            description: Optional table description
            table_schema: Optional JSON Schema for document validation
            scope: Organization scope override. Omit to use the execution
                context org. Pass an org UUID to target a specific org
                (provider orgs only). Pass None explicitly for global scope.
            app: Application UUID to scope table to a specific app

        Returns:
            TableInfo: Created table metadata

        Raises:
            RuntimeError: If not authenticated
            HTTPError: If table already exists (409) or other API error

        Example:
            >>> from bifrost import tables
            >>> table = await tables.create("customers")
            >>> app_table = await tables.create("app_data", app="app-uuid")
        """
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/cli/tables/create",
            json={
                "name": name,
                "description": description,
                "table_schema": table_schema,
                "scope": effective_scope,
                "app": app,
            }
        )
        raise_for_status_with_detail(response)
        return TableInfo.model_validate(response.json())

    @staticmethod
    async def list(
        scope: str | None = None,
        app: str | None = None,
    ) -> list[TableInfo]:
        """
        List all tables in the current scope.

        Args:
            scope: Organization scope override. Omit to use the execution
                context org (includes global tables via cascade).
                Pass an org UUID to target a specific org (provider orgs only).
                Pass None explicitly for global scope only.
            app: Filter by application UUID

        Returns:
            list[TableInfo]: List of table metadata

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import tables
            >>> all_tables = await tables.list()
            >>> app_tables = await tables.list(app="app-uuid")
        """
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/cli/tables/list",
            json={
                "scope": effective_scope,
                "app": app,
            }
        )
        raise_for_status_with_detail(response)
        return [TableInfo.model_validate(t) for t in response.json()]

    @staticmethod
    async def delete(
        table_id: str,
    ) -> bool:
        """
        Delete a table and all its documents.

        Args:
            table_id: Table UUID

        Returns:
            bool: True if deleted successfully

        Raises:
            RuntimeError: If not authenticated
            HTTPError: If table not found (404)

        Example:
            >>> from bifrost import tables
            >>> await tables.delete("table-uuid-here")
        """
        client = get_client()
        response = await client.delete(
            f"/api/tables/{table_id}",
        )
        raise_for_status_with_detail(response)
        return True

    # =========================================================================
    # Document Operations
    # =========================================================================

    @staticmethod
    async def insert(
        table: str,
        data: dict[str, Any],
        id: str | None = None,
        scope: str | None = None,
        created_by: str | None = None,
    ) -> DocumentData:
        """
        Insert a document into a table.

        Auto-creates the table on the first write (404 → POST /api/tables → retry).

        Args:
            table: Table name or UUID.
            data: Document data (JSON-serializable dict).
            id: Document ID (user-provided key). If not provided, a UUID is
                auto-generated server-side.
            scope: Organization scope. Defaults to the execution context org.
            created_by: Override attribution. Engine and platform-admin
                callers only. Defaults to the workflow's calling user.

        Returns:
            DocumentData: Created document with ID and timestamps.

        Example:
            >>> from bifrost import tables
            >>> doc = await tables.insert("customers", {"name": "Acme Corp"})
            >>> doc = await tables.insert("customers", id="acme-001", data={...})
        """
        if created_by is None:
            ctx = _current_context()
            if ctx is not None and getattr(ctx, "user_id", None) is not None:
                created_by = str(ctx.user_id)
        effective_scope = resolve_scope(scope)
        body: dict[str, Any] = {"data": data, "id": id}
        if created_by is not None:
            body["created_by"] = created_by

        client = get_client()
        url = f"/api/tables/{table}/documents{_scope_query(effective_scope)}"
        response = await client.post(url, json=body)
        if response.status_code == 404:
            # Table doesn't exist — auto-create then retry.
            await _ensure_table_exists(table, effective_scope)
            response = await client.post(url, json=body)
        raise_for_status_with_detail(response)
        return DocumentData.model_validate(response.json())

    @staticmethod
    async def upsert(
        table: str,
        id: str,
        data: dict[str, Any],
        scope: str | None = None,
        created_by: str | None = None,
        updated_by: str | None = None,
    ) -> DocumentData:
        """
        Upsert (create or replace) a document atomically by id.

        Auto-creates the table on the first write. On conflict the JSONB
        ``data`` column is **replaced**, not merged — use ``update`` for
        partial updates with merge semantics.

        Args:
            table: Table name or UUID.
            id: Document ID (the upsert conflict key — required).
            data: Document data (JSON-serializable dict).
            scope: Organization scope.
            created_by: Override attribution on insert. Engine/admin only.
            updated_by: Override attribution on insert and update. Engine/admin only.

        Returns:
            DocumentData: Created or updated document.

        Example:
            >>> doc = await tables.upsert("employees", id="john@example.com",
            ...                            data={"name": "John Doe"})
        """
        ctx = _current_context()
        if created_by is None and ctx is not None and getattr(ctx, "user_id", None) is not None:
            created_by = str(ctx.user_id)
        if updated_by is None and ctx is not None and getattr(ctx, "user_id", None) is not None:
            updated_by = str(ctx.user_id)
        effective_scope = resolve_scope(scope)
        body: dict[str, Any] = {"id": id, "data": data}
        if created_by is not None:
            body["created_by"] = created_by
        if updated_by is not None:
            body["updated_by"] = updated_by

        client = get_client()
        url = f"/api/tables/{table}/documents/upsert{_scope_query(effective_scope)}"
        response = await client.post(url, json=body)
        if response.status_code == 404:
            await _ensure_table_exists(table, effective_scope)
            response = await client.post(url, json=body)
        raise_for_status_with_detail(response)
        return DocumentData.model_validate(response.json())

    @staticmethod
    async def get(
        table: str,
        doc_id: str,
        scope: str | None = None,
    ) -> DocumentData | None:
        """
        Get a document by ID.

        Args:
            table: Table name or UUID.
            doc_id: Document ID.
            scope: Organization scope.

        Returns:
            DocumentData if found, None if not found.

        Example:
            >>> doc = await tables.get("customers", "acme-001")
        """
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.get(
            f"/api/tables/{table}/documents/{doc_id}{_scope_query(effective_scope)}",
        )
        if response.status_code == 404:
            return None
        raise_for_status_with_detail(response)
        data = response.json()
        if data is None:
            return None
        return DocumentData.model_validate(data)

    @staticmethod
    async def update(
        table: str,
        doc_id: str,
        data: dict[str, Any],
        scope: str | None = None,
        updated_by: str | None = None,
    ) -> DocumentData | None:
        """
        Update a document (partial update, merges with existing data).

        Args:
            table: Table name or UUID.
            doc_id: Document ID.
            data: Fields to update (merged with the existing JSONB data).
            scope: Organization scope.
            updated_by: Override attribution. Engine and platform-admin only.

        Returns:
            DocumentData if updated, None if not found.

        Example:
            >>> doc = await tables.update("customers", "acme-001", {"status": "inactive"})
        """
        if updated_by is None:
            ctx = _current_context()
            if ctx is not None and getattr(ctx, "user_id", None) is not None:
                updated_by = str(ctx.user_id)
        client = get_client()
        effective_scope = resolve_scope(scope)
        body: dict[str, Any] = {"data": data}
        if updated_by is not None:
            body["updated_by"] = updated_by
        response = await client.patch(
            f"/api/tables/{table}/documents/{doc_id}{_scope_query(effective_scope)}",
            json=body,
        )
        if response.status_code == 404:
            return None
        raise_for_status_with_detail(response)
        result = response.json()
        if result is None:
            return None
        return DocumentData.model_validate(result)

    @staticmethod
    async def delete_document(
        table: str,
        doc_id: str,
        scope: str | None = None,
    ) -> bool:
        """
        Delete a document.

        Args:
            table: Table name or UUID.
            doc_id: Document ID.
            scope: Organization scope.

        Returns:
            bool: True if deleted, False if not found.

        Example:
            >>> deleted = await tables.delete_document("customers", "acme-001")
        """
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.delete(
            f"/api/tables/{table}/documents/{doc_id}{_scope_query(effective_scope)}",
        )
        if response.status_code == 404:
            return False
        raise_for_status_with_detail(response)
        return True

    # =========================================================================
    # Batch Document Operations
    # =========================================================================

    @staticmethod
    async def insert_batch(
        table: str,
        documents: list[dict[str, Any]],
        scope: str | None = None,
        created_by: str | None = None,
    ) -> BatchResult:
        """
        Batch insert multiple documents into a table.

        Auto-creates the table on the first write. All-or-nothing on policy
        denials: a single denied row fails the whole batch with 403.

        Args:
            table: Table name or UUID.
            documents: List of documents to insert. Each dict can be:
                - A raw data dict (ID will be auto-generated)
                - A dict with "id" and "data" keys for explicit ID control
            scope: Organization scope.
            created_by: Override attribution applied to every item.
                Engine and platform-admin only.

        Returns:
            BatchResult: Inserted documents and count.

        Example:
            >>> result = await tables.insert_batch("customers", [
            ...     {"name": "Acme Corp", "status": "active"},
            ...     {"id": "beta-001", "data": {"name": "Beta Inc"}},
            ... ])
        """
        return await tables._batch_write(
            table=table,
            documents=documents,
            scope=scope,
            upsert=False,
            created_by=created_by,
            updated_by=None,
        )

    @staticmethod
    async def upsert_batch(
        table: str,
        documents: list[dict[str, Any]],
        scope: str | None = None,
        created_by: str | None = None,
        updated_by: str | None = None,
    ) -> BatchResult:
        """
        Batch upsert (create or update on conflict) multiple documents.

        Auto-creates the table on the first write. Each document must have
        an "id" and "data" key. All-or-nothing on policy denials.

        Args:
            table: Table name or UUID.
            documents: List of dicts, each with "id" (str) and "data" (dict).
            scope: Organization scope.
            created_by: Override attribution on insert paths. Engine/admin only.
            updated_by: Override attribution on insert and update paths.
                Engine/admin only.

        Returns:
            BatchResult: Inserted/updated documents and count.

        Example:
            >>> result = await tables.upsert_batch("employees", [
            ...     {"id": "john@co.com", "data": {"name": "John"}},
            ...     {"id": "jane@co.com", "data": {"name": "Jane"}},
            ... ])
        """
        return await tables._batch_write(
            table=table,
            documents=documents,
            scope=scope,
            upsert=True,
            created_by=created_by,
            updated_by=updated_by,
        )

    @staticmethod
    async def _batch_write(
        table: str,
        documents: list[dict[str, Any]],
        scope: str | None,
        upsert: bool,
        created_by: str | None,
        updated_by: str | None,
    ) -> BatchResult:
        """Shared insert+upsert batch path against ``POST /documents/batch``.

        404 → auto-create table → retry once. The created_by/updated_by
        override is applied per-item so the engine can attribute writes to
        the workflow's calling user.
        """
        ctx = _current_context()
        if created_by is None and ctx is not None and getattr(ctx, "user_id", None) is not None:
            created_by = str(ctx.user_id)
        if upsert and updated_by is None and ctx is not None and getattr(ctx, "user_id", None) is not None:
            updated_by = str(ctx.user_id)
        effective_scope = resolve_scope(scope)

        items: list[dict[str, Any]] = []
        for doc in documents:
            if "data" in doc and isinstance(doc["data"], dict):
                item: dict[str, Any] = {"id": doc.get("id"), "data": doc["data"]}
            else:
                item = {"id": None, "data": doc}
            if created_by is not None:
                item["created_by"] = created_by
            if upsert and updated_by is not None:
                item["updated_by"] = updated_by
            items.append(item)

        req_body: dict[str, Any] = {"documents": items, "upsert": upsert}
        client = get_client()
        url = f"/api/tables/{table}/documents/batch{_scope_query(effective_scope)}"
        response = await client.post(url, json=req_body)
        if response.status_code == 404:
            await _ensure_table_exists(table, effective_scope)
            response = await client.post(url, json=req_body)
        raise_for_status_with_detail(response)
        body = response.json()
        return BatchResult(
            documents=[DocumentData.model_validate(d) for d in body.get("documents", [])],
            count=body["inserted"],
        )

    @staticmethod
    async def delete_batch(
        table: str,
        doc_ids: list[str],
        scope: str | None = None,
    ) -> BatchDeleteResult:
        """
        Batch delete multiple documents by ID.

        Non-existent IDs are silently skipped. All-or-nothing on policy
        denials: a single denied id fails the whole batch with 403.

        Args:
            table: Table name or UUID.
            doc_ids: List of document IDs to delete.
            scope: Organization scope.

        Returns:
            BatchDeleteResult: Deleted IDs and count.

        Example:
            >>> result = await tables.delete_batch("customers", ["acme-001", "beta-001"])
        """
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.post(
            f"/api/tables/{table}/documents/batch-delete{_scope_query(effective_scope)}",
            json={"ids": doc_ids},
        )
        if response.status_code == 404:
            return BatchDeleteResult(deleted_ids=[], count=0)
        raise_for_status_with_detail(response)
        body = response.json()
        return BatchDeleteResult(
            deleted_ids=body.get("deleted_ids", []),
            count=body["deleted"],
        )

    @staticmethod
    async def query(
        table: str,
        where: dict[str, Any] | None = None,
        order_by: str | None = None,
        order_dir: str = "asc",
        limit: int = 100,
        offset: int = 0,
        scope: str | None = None,
    ) -> DocumentList:
        """
        Query documents with filtering and pagination.

        Supports advanced filter operators:
        - Simple equality: {"status": "active"}
        - Comparison: {"amount": {"gt": 100, "lte": 1000}}
        - LIKE patterns: {"name": {"contains": "acme"}}
        - IN lists: {"category": {"in_": ["a", "b"]}}
        - NULL checks: {"deleted_at": {"is_null": True}}

        Args:
            table: Table name or UUID.
            where: Filter conditions with optional operators.
            order_by: Field name to order by (JSONB field).
            order_dir: "asc" or "desc".
            limit: Maximum documents to return (default 100).
            offset: Number of documents to skip.
            scope: Organization scope.

        Returns:
            DocumentList: Query results with documents, total count, and
            pagination info. Returns an empty list if the table doesn't exist.

        Example:
            >>> results = await tables.query("customers", where={"status": "active"})
        """
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.post(
            f"/api/tables/{table}/documents/query{_scope_query(effective_scope)}",
            json={
                "where": where,
                "order_by": order_by,
                "order_dir": order_dir,
                "limit": limit,
                "offset": offset,
            },
        )
        if response.status_code == 404:
            return DocumentList(documents=[], total=0, limit=limit, offset=offset)
        raise_for_status_with_detail(response)
        return DocumentList.model_validate(response.json())

    @staticmethod
    async def count(
        table: str,
        where: dict[str, Any] | None = None,
        scope: str | None = None,
    ) -> int:
        """
        Count documents in a table, optionally with filtering.

        Args:
            table: Table name or UUID.
            where: Filter conditions (same operators as ``query``).
            scope: Organization scope.

        Returns:
            int: Number of matching documents. Returns 0 if the table
            doesn't exist.

        Example:
            >>> total = await tables.count("customers")
            >>> active = await tables.count("customers", where={"status": "active"})
        """
        effective_scope = resolve_scope(scope)
        client = get_client()
        if where is None:
            # Unfiltered count: hit GET /count which avoids row scanning.
            response = await client.get(
                f"/api/tables/{table}/documents/count{_scope_query(effective_scope)}",
            )
            if response.status_code == 404:
                return 0
            raise_for_status_with_detail(response)
            return response.json()["count"]

        # Filtered count: REST /count doesn't accept where, so fall back to
        # query with limit=1 (the response carries the matched total).
        result = await tables.query(
            table=table, where=where, limit=1, scope=scope,
        )
        return result.total
