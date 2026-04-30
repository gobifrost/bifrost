"""
Tables SDK for Bifrost - API-only implementation.

Provides Python API for table and document management (CRUD operations).
All operations go through HTTP API endpoints.
All methods are async and must be awaited.
"""

from __future__ import annotations

from typing import Any

from .client import get_client, raise_for_status_with_detail
from .models import TableInfo, DocumentData, DocumentList, BatchResult, BatchDeleteResult
from ._context import resolve_scope, _execution_context


def _current_context():
    """Return the active ExecutionContext, or None if not in a workflow execution."""
    return _execution_context.get()


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
        app: str | None = None,
        created_by: str | None = None,
    ) -> DocumentData:
        """
        Insert a document into a table.

        Auto-creates the table if it doesn't exist.

        Args:
            table: Table name
            data: Document data (JSON-serializable dict)
            id: Document ID (user-provided key). If not provided, a UUID is auto-generated.
            scope: Organization scope
            app: Application UUID

        Returns:
            DocumentData: Created document with ID and timestamps

        Raises:
            RuntimeError: If not authenticated
            HTTPError: If document with id already exists (409 Conflict)

        Example:
            >>> from bifrost import tables
            >>> # Auto-generated ID
            >>> doc = await tables.insert("customers", {
            ...     "name": "Acme Corp",
            ...     "email": "info@acme.com",
            ... })
            >>> # User-provided ID
            >>> doc = await tables.insert("customers", id="acme-001", data={
            ...     "name": "Acme Corp",
            ...     "email": "info@acme.com",
            ... })
        """
        if created_by is None:
            ctx = _current_context()
            if ctx is not None and getattr(ctx, "user_id", None) is not None:
                created_by = str(ctx.user_id)
        client = get_client()
        effective_scope = resolve_scope(scope)
        body: dict[str, Any] = {
            "table": table,
            "data": data,
            "id": id,
            "scope": effective_scope,
            "app": app,
        }
        if created_by is not None:
            body["created_by"] = created_by
        response = await client.post(
            "/api/cli/tables/documents/insert",
            json=body,
        )
        raise_for_status_with_detail(response)
        return DocumentData.model_validate(response.json())

    @staticmethod
    async def upsert(
        table: str,
        id: str,
        data: dict[str, Any],
        scope: str | None = None,
        app: str | None = None,
        created_by: str | None = None,
        updated_by: str | None = None,
    ) -> DocumentData:
        """
        Upsert (create or replace) a document.

        Auto-creates the table if it doesn't exist.
        If a document with the given id exists, it is replaced with the new data.
        If not, a new document is created.

        Args:
            table: Table name
            id: Document ID (required for upsert)
            data: Document data (JSON-serializable dict)
            scope: Organization scope
            app: Application UUID

        Returns:
            DocumentData: Created or updated document with ID and timestamps

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import tables
            >>> doc = await tables.upsert("employees", id="john@example.com", data={
            ...     "name": "John Doe",
            ...     "department": "Engineering",
            ... })
        """
        ctx = _current_context()
        if created_by is None and ctx is not None and getattr(ctx, "user_id", None) is not None:
            created_by = str(ctx.user_id)
        if updated_by is None and ctx is not None and getattr(ctx, "user_id", None) is not None:
            updated_by = str(ctx.user_id)
        client = get_client()
        effective_scope = resolve_scope(scope)
        body: dict[str, Any] = {
            "table": table,
            "id": id,
            "data": data,
            "scope": effective_scope,
            "app": app,
        }
        if created_by is not None:
            body["created_by"] = created_by
        if updated_by is not None:
            body["updated_by"] = updated_by
        response = await client.post(
            "/api/cli/tables/documents/upsert",
            json=body,
        )
        raise_for_status_with_detail(response)
        return DocumentData.model_validate(response.json())

    @staticmethod
    async def get(
        table: str,
        doc_id: str,
        scope: str | None = None,
        app: str | None = None,
    ) -> DocumentData | None:
        """
        Get a document by ID.

        Args:
            table: Table name
            doc_id: Document ID (user-provided or auto-generated)
            scope: Organization scope
            app: Application UUID

        Returns:
            DocumentData if found, None if not found

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import tables
            >>> doc = await tables.get("customers", "acme-001")
        """
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/cli/tables/documents/get",
            json={
                "table": table,
                "doc_id": doc_id,
                "scope": effective_scope,
                "app": app,
            }
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
        app: str | None = None,
        updated_by: str | None = None,
    ) -> DocumentData | None:
        """
        Update a document (partial update, merges with existing).

        Args:
            table: Table name
            doc_id: Document UUID
            data: Fields to update (merged with existing data)
            scope: Organization scope
            app: Application UUID

        Returns:
            DocumentData if updated, None if not found

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import tables
            >>> doc = await tables.update("customers", "uuid-here", {"status": "inactive"})
        """
        if updated_by is None:
            ctx = _current_context()
            if ctx is not None and getattr(ctx, "user_id", None) is not None:
                updated_by = str(ctx.user_id)
        client = get_client()
        effective_scope = resolve_scope(scope)
        body: dict[str, Any] = {
            "table": table,
            "doc_id": doc_id,
            "data": data,
            "scope": effective_scope,
            "app": app,
        }
        if updated_by is not None:
            body["updated_by"] = updated_by
        response = await client.post(
            "/api/cli/tables/documents/update",
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
        app: str | None = None,
    ) -> bool:
        """
        Delete a document.

        Args:
            table: Table name
            doc_id: Document UUID
            scope: Organization scope
            app: Application UUID

        Returns:
            bool: True if deleted, False if not found

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import tables
            >>> deleted = await tables.delete_document("customers", "uuid-here")
        """
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/cli/tables/documents/delete",
            json={
                "table": table,
                "doc_id": doc_id,
                "scope": effective_scope,
                "app": app,
            }
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
        app: str | None = None,
        created_by: str | None = None,
    ) -> BatchResult:
        """
        Batch insert multiple documents into a table.

        Auto-creates the table if it doesn't exist.
        All documents are inserted atomically — if any ID conflicts, the entire batch rolls back.

        Args:
            table: Table name
            documents: List of documents to insert. Each dict can be:
                - A raw data dict (ID will be auto-generated)
                - A dict with "id" and "data" keys for explicit ID control
            scope: Organization scope
            app: Application UUID

        Returns:
            BatchResult: Created documents and count

        Raises:
            RuntimeError: If not authenticated
            HTTPError: If any document ID already exists (409 Conflict)
            HTTPError: If more than 1000 documents (422)

        Example:
            >>> from bifrost import tables
            >>> # Auto-generated IDs
            >>> result = await tables.insert_batch("customers", [
            ...     {"name": "Acme Corp", "status": "active"},
            ...     {"name": "Beta Inc", "status": "pending"},
            ... ])
            >>> print(result.count)  # 2
            >>>
            >>> # Explicit IDs
            >>> result = await tables.insert_batch("customers", [
            ...     {"id": "acme-001", "data": {"name": "Acme Corp"}},
            ...     {"id": "beta-001", "data": {"name": "Beta Inc"}},
            ... ])
        """
        if created_by is None:
            ctx = _current_context()
            if ctx is not None and getattr(ctx, "user_id", None) is not None:
                created_by = str(ctx.user_id)
        client = get_client()
        effective_scope = resolve_scope(scope)

        # Normalize items: if no "data" key, the entire dict becomes the data
        items = []
        for doc in documents:
            if "data" in doc and isinstance(doc["data"], dict):
                items.append({"id": doc.get("id"), "data": doc["data"]})
            else:
                items.append({"id": None, "data": doc})

        req_body: dict[str, Any] = {
            "table": table,
            "documents": items,
            "scope": effective_scope,
            "app": app,
        }
        if created_by is not None:
            req_body["created_by"] = created_by
        response = await client.post(
            "/api/cli/tables/documents/insert/batch",
            json=req_body,
        )
        raise_for_status_with_detail(response)
        body = response.json()
        return BatchResult(
            documents=[DocumentData.model_validate(d) for d in body["documents"]],
            count=body["count"],
        )

    @staticmethod
    async def upsert_batch(
        table: str,
        documents: list[dict[str, Any]],
        scope: str | None = None,
        app: str | None = None,
        created_by: str | None = None,
        updated_by: str | None = None,
    ) -> BatchResult:
        """
        Batch upsert (create or replace) multiple documents.

        Auto-creates the table if it doesn't exist.
        Each document must have an "id" and "data" key.

        Args:
            table: Table name
            documents: List of dicts, each with "id" (str) and "data" (dict) keys
            scope: Organization scope
            app: Application UUID

        Returns:
            BatchResult: Created/updated documents and count

        Raises:
            RuntimeError: If not authenticated
            HTTPError: If more than 1000 documents (422)

        Example:
            >>> from bifrost import tables
            >>> result = await tables.upsert_batch("employees", [
            ...     {"id": "john@co.com", "data": {"name": "John", "dept": "Eng"}},
            ...     {"id": "jane@co.com", "data": {"name": "Jane", "dept": "Sales"}},
            ... ])
            >>> print(result.count)  # 2
        """
        ctx = _current_context()
        if created_by is None and ctx is not None and getattr(ctx, "user_id", None) is not None:
            created_by = str(ctx.user_id)
        if updated_by is None and ctx is not None and getattr(ctx, "user_id", None) is not None:
            updated_by = str(ctx.user_id)
        client = get_client()
        effective_scope = resolve_scope(scope)

        items = [{"id": doc["id"], "data": doc["data"]} for doc in documents]

        req_body: dict[str, Any] = {
            "table": table,
            "documents": items,
            "scope": effective_scope,
            "app": app,
        }
        if created_by is not None:
            req_body["created_by"] = created_by
        if updated_by is not None:
            req_body["updated_by"] = updated_by
        response = await client.post(
            "/api/cli/tables/documents/upsert/batch",
            json=req_body,
        )
        raise_for_status_with_detail(response)
        body = response.json()
        return BatchResult(
            documents=[DocumentData.model_validate(d) for d in body["documents"]],
            count=body["count"],
        )

    @staticmethod
    async def delete_batch(
        table: str,
        doc_ids: list[str],
        scope: str | None = None,
        app: str | None = None,
    ) -> BatchDeleteResult:
        """
        Batch delete multiple documents by ID.

        Non-existent IDs are silently skipped.

        Args:
            table: Table name
            doc_ids: List of document IDs to delete
            scope: Organization scope
            app: Application UUID

        Returns:
            BatchDeleteResult: Deleted IDs and count

        Raises:
            RuntimeError: If not authenticated
            HTTPError: If more than 1000 IDs (422)

        Example:
            >>> from bifrost import tables
            >>> result = await tables.delete_batch("customers", ["acme-001", "beta-001"])
            >>> print(result.count)  # 2
            >>> print(result.deleted_ids)  # ["acme-001", "beta-001"]
        """
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/cli/tables/documents/delete/batch",
            json={
                "table": table,
                "doc_ids": doc_ids,
                "scope": effective_scope,
                "app": app,
            }
        )
        raise_for_status_with_detail(response)
        body = response.json()
        return BatchDeleteResult(
            deleted_ids=body["deleted_ids"],
            count=body["count"],
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
        app: str | None = None,
    ) -> DocumentList:
        """
        Query documents with filtering and pagination.

        Supports advanced filter operators:
        - Simple equality: {"status": "active"}
        - Comparison: {"amount": {"gt": 100, "lte": 1000}}
        - LIKE patterns: {"name": {"like": "%acme%"}} or {"name": {"ilike": "%ACME%"}}
        - IN lists: {"category": {"in": ["a", "b"]}}
        - NULL checks: {"deleted_at": {"is_null": True}}

        Args:
            table: Table name
            where: Filter conditions with optional operators
            order_by: Field name to order by (JSONB field)
            order_dir: "asc" or "desc"
            limit: Maximum documents to return (default 100)
            offset: Number of documents to skip
            scope: Organization scope
            app: Application UUID

        Returns:
            DocumentList: Query results with documents, total count, and pagination info.
            Returns empty list if table doesn't exist.

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import tables
            >>> # Simple equality filter
            >>> results = await tables.query("customers", where={"status": "active"})
            >>>
            >>> # Range query
            >>> results = await tables.query(
            ...     "orders",
            ...     where={"amount": {"gte": 100, "lt": 1000}}
            ... )
            >>>
            >>> # Case-insensitive search
            >>> results = await tables.query(
            ...     "customers",
            ...     where={"name": {"ilike": "%acme%"}}
            ... )
        """
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/cli/tables/documents/query",
            json={
                "table": table,
                "where": where,
                "order_by": order_by,
                "order_dir": order_dir,
                "limit": limit,
                "offset": offset,
                "scope": effective_scope,
                "app": app,
            }
        )
        # Return empty result if table doesn't exist
        if response.status_code == 404:
            return DocumentList(documents=[], total=0, limit=limit, offset=offset)
        raise_for_status_with_detail(response)
        return DocumentList.model_validate(response.json())

    @staticmethod
    async def count(
        table: str,
        where: dict[str, Any] | None = None,
        scope: str | None = None,
        app: str | None = None,
    ) -> int:
        """
        Count documents in a table, optionally with filtering.

        Supports the same filter operators as query():
        - Simple equality: {"status": "active"}
        - Comparison: {"amount": {"gt": 100, "lte": 1000}}
        - LIKE patterns: {"name": {"like": "%acme%"}} or {"name": {"ilike": "%ACME%"}}
        - IN lists: {"category": {"in": ["a", "b"]}}
        - NULL checks: {"deleted_at": {"is_null": True}}

        Args:
            table: Table name
            where: Filter conditions with optional operators
            scope: Organization scope
            app: Application UUID

        Returns:
            int: Number of matching documents. Returns 0 if table doesn't exist.

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import tables
            >>> total = await tables.count("customers")
            >>> active = await tables.count("customers", where={"status": "active"})
        """
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/cli/tables/documents/count",
            json={
                "table": table,
                "where": where,
                "scope": effective_scope,
                "app": app,
            }
        )
        # Return 0 if table doesn't exist
        if response.status_code == 404:
            return 0
        raise_for_status_with_detail(response)
        return response.json()
