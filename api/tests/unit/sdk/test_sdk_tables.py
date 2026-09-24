"""
Unit tests for Bifrost Tables SDK module.

Tests SDK module structure and API method signatures.
Integration tests for actual API calls are in tests/integration/platform/.
"""

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestTablesSDKImports:
    """Test that tables SDK can be imported correctly."""

    def test_import_bifrost_tables(self):
        """Test importing tables module."""
        from bifrost import tables

        # Verify module has expected methods
        assert hasattr(tables, 'create')
        assert hasattr(tables, 'list')
        assert hasattr(tables, 'delete')
        assert hasattr(tables, 'insert')
        assert hasattr(tables, 'get')
        assert hasattr(tables, 'update')
        assert hasattr(tables, 'delete_document')
        assert hasattr(tables, 'query')
        assert hasattr(tables, 'count')

    def test_import_bifrost_tables_batch_methods(self):
        """Test importing batch methods from tables module."""
        from bifrost import tables

        assert hasattr(tables, 'insert_batch')
        assert hasattr(tables, 'upsert_batch')
        assert hasattr(tables, 'bulk_upsert')
        assert hasattr(tables, 'delete_batch')

    def test_import_table_models(self):
        """Test importing table models."""
        from bifrost import TableInfo, DocumentData, DocumentList

        assert TableInfo is not None
        assert DocumentData is not None
        assert DocumentList is not None

    def test_import_batch_models(self):
        """Test importing batch result models."""
        from bifrost import BatchResult, BatchDeleteResult, BulkUpsertResult

        assert BatchResult is not None
        assert BatchDeleteResult is not None
        assert BulkUpsertResult is not None

    def test_table_info_model_fields(self):
        """Test TableInfo model has expected fields."""
        from bifrost import TableInfo

        # Create instance to verify fields work
        table = TableInfo(
            id="test-id",
            name="customers",
            description="Customer data",
            table_schema={"type": "object"},
            organization_id="org-123",
            created_by="test@example.com",
        )

        assert table.id == "test-id"
        assert table.name == "customers"
        assert table.description == "Customer data"
        assert table.table_schema == {"type": "object"}
        assert table.organization_id == "org-123"

    def test_document_data_model_fields(self):
        """Test DocumentData model has expected fields."""
        from bifrost import DocumentData

        doc = DocumentData(
            id="doc-id",
            table_id="table-id",
            data={"name": "Acme", "status": "active"},
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )

        assert doc.id == "doc-id"
        assert doc.table_id == "table-id"
        assert doc.data["name"] == "Acme"
        assert doc.data["status"] == "active"

    def test_document_list_model_fields(self):
        """Test DocumentList model has expected fields."""
        from bifrost import DocumentData, DocumentList

        doc = DocumentData(
            id="doc-id",
            table_id="table-id",
            data={"name": "Acme"},
        )

        doc_list = DocumentList(
            documents=[doc],
            total=100,
            limit=10,
            offset=0,
        )

        assert len(doc_list.documents) == 1
        assert doc_list.total == 100
        assert doc_list.limit == 10
        assert doc_list.offset == 0


    def test_batch_result_model_fields(self):
        """Test BatchResult model has expected fields."""
        from bifrost import BatchResult, DocumentData

        doc = DocumentData(
            id="doc-id",
            table_id="table-id",
            data={"name": "Acme"},
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )

        result = BatchResult(documents=[doc], count=1)

        assert len(result.documents) == 1
        assert result.count == 1
        assert result.documents[0].id == "doc-id"

    def test_batch_delete_result_model_fields(self):
        """Test BatchDeleteResult model has expected fields."""
        from bifrost import BatchDeleteResult

        result = BatchDeleteResult(deleted_ids=["id-1", "id-2"], count=2)

        assert result.deleted_ids == ["id-1", "id-2"]
        assert result.count == 2

    def test_bulk_upsert_result_model_fields(self):
        """Test BulkUpsertResult model has expected fields."""
        from bifrost import BulkUpsertResult

        result = BulkUpsertResult(count=3)

        assert result.count == 3


@pytest.mark.asyncio
async def test_tables_bulk_upsert_posts_replace_upsert_batch_request(monkeypatch):
    module = importlib.import_module("bifrost.tables")
    from bifrost import tables

    response = MagicMock(status_code=200)
    response.json.return_value = {"inserted": 2, "documents": []}
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    monkeypatch.setattr(module, "get_client", lambda: client)
    monkeypatch.setattr(module, "raise_for_status_with_detail", MagicMock())

    result = await tables.bulk_upsert(
        "customers",
        [
            {"id": "a", "data": {"x": 1}},
            {"id": "b", "data": {"y": None}},
        ],
        scope="global",
        created_by="importer",
        updated_by="importer",
    )

    assert result.count == 2
    client.post.assert_awaited_once_with(
        "/api/tables/customers/documents/batch?scope=global",
        json={
            "write_mode": "replace_upsert",
            "return_documents": False,
            "documents": [
                {
                    "id": "a",
                    "data": {"x": 1},
                    "created_by": "importer",
                    "updated_by": "importer",
                },
                {
                    "id": "b",
                    "data": {"y": None},
                    "created_by": "importer",
                    "updated_by": "importer",
                },
            ]
        },
    )
    assert "/documents/bulk-upsert" not in client.post.await_args.args[0]


@pytest.mark.asyncio
async def test_tables_query_forwards_document_id_pagination_and_skip_count(monkeypatch):
    module = importlib.import_module("bifrost.tables")
    from bifrost import tables

    response = MagicMock(status_code=200)
    response.json.return_value = {
        "documents": [],
        "total": -1,
        "limit": 500,
        "offset": 0,
    }
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    monkeypatch.setattr(module, "get_client", lambda: client)
    monkeypatch.setattr(module, "raise_for_status_with_detail", MagicMock())

    result = await tables.query(
        "inventory",
        scope="global",
        after_document_id="tenant|drive|item-001",
        document_id_prefix="tenant|",
        document_ids=["tenant|drive|item-001", "tenant|drive|item-002"],
        skip_count=True,
        limit=500,
    )

    assert result.total == -1
    client.post.assert_awaited_once_with(
        "/api/tables/inventory/documents/query?scope=global",
        json={
            "where": None,
            "order_by": None,
            "order_dir": "asc",
            "limit": 500,
            "offset": 0,
            "after_document_id": "tenant|drive|item-001",
            "document_id_prefix": "tenant|",
            "document_ids": ["tenant|drive|item-001", "tenant|drive|item-002"],
            "skip_count": True,
        },
        retry_transient=True,
    )


@pytest.mark.asyncio
async def test_tables_bulk_upsert_retries_bounded_conflict(monkeypatch):
    module = importlib.import_module("bifrost.tables")
    from bifrost import tables

    conflict = MagicMock(status_code=409)
    success = MagicMock(status_code=200)
    success.json.return_value = {"inserted": 1, "documents": []}
    client = MagicMock()
    client.post = AsyncMock(side_effect=[conflict, success])
    monkeypatch.setattr(module, "get_client", lambda: client)
    monkeypatch.setattr(module, "raise_for_status_with_detail", MagicMock())

    result = await tables.bulk_upsert(
        "customers",
        [{"id": "a", "data": {"x": 1}}],
        conflict_retries=1,
    )

    assert result.count == 1
    assert client.post.await_count == 2


@pytest.mark.parametrize("method_name", ["insert_batch", "upsert_batch", "bulk_upsert"])
@pytest.mark.asyncio
async def test_tables_batch_writes_reject_more_than_1000_before_http(method_name, monkeypatch):
    module = importlib.import_module("bifrost.tables")
    from bifrost import tables

    client = MagicMock()
    client.post = AsyncMock()
    monkeypatch.setattr(module, "get_client", lambda: client)
    documents = [{"id": f"doc-{index}", "data": {"index": index}} for index in range(1001)]

    with pytest.raises(ValueError) as exc:
        await getattr(tables, method_name)("customers", documents)

    assert str(exc.value) == "table batch writes accept at most 1000 documents"
    client.post.assert_not_called()


@pytest.mark.parametrize(
    ("method_name", "documents", "expected_count"),
    [
        ("insert_batch", [], 0),
        ("upsert_batch", [], 0),
        ("bulk_upsert", [], 0),
        ("insert_batch", [{"id": f"doc-{index}", "data": {"index": index}} for index in range(1000)], 1000),
        ("upsert_batch", [{"id": f"doc-{index}", "data": {"index": index}} for index in range(1000)], 1000),
        ("bulk_upsert", [{"id": f"doc-{index}", "data": {"index": index}} for index in range(1000)], 1000),
    ],
)
@pytest.mark.asyncio
async def test_tables_batch_writes_allow_empty_and_1000_documents(
    method_name,
    documents,
    expected_count,
    monkeypatch,
):
    module = importlib.import_module("bifrost.tables")
    from bifrost import tables

    response = MagicMock(status_code=200)
    response.json.return_value = {"inserted": expected_count, "documents": []}
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    monkeypatch.setattr(module, "get_client", lambda: client)
    monkeypatch.setattr(module, "raise_for_status_with_detail", MagicMock())

    result = await getattr(tables, method_name)("customers", documents)

    assert result.count == expected_count
    client.post.assert_awaited_once()


class TestTablesSDKWithoutContext:
    """Test SDK behavior without execution context."""

    @pytest.mark.asyncio
    async def test_tables_create_without_context_raises_error(self):
        """Test that tables.create raises error when not authenticated."""
        from bifrost import tables
        from bifrost.client import _clear_client
        from bifrost._context import clear_execution_context

        # Ensure no context is set and no client injected
        clear_execution_context()
        _clear_client()

        # Attempting to use SDK should raise RuntimeError about not being logged in
        with pytest.raises(RuntimeError, match="Not logged in"):
            await tables.create("test_table")

    @pytest.mark.asyncio
    async def test_tables_list_without_context_raises_error(self):
        """Test that tables.list raises error when not authenticated."""
        from bifrost import tables
        from bifrost.client import _clear_client
        from bifrost._context import clear_execution_context

        clear_execution_context()
        _clear_client()

        with pytest.raises(RuntimeError, match="Not logged in"):
            await tables.list()

    @pytest.mark.asyncio
    async def test_tables_insert_without_context_raises_error(self):
        """Test that tables.insert raises error when not authenticated."""
        from bifrost import tables
        from bifrost.client import _clear_client
        from bifrost._context import clear_execution_context

        clear_execution_context()
        _clear_client()

        with pytest.raises(RuntimeError, match="Not logged in"):
            await tables.insert("customers", {"name": "Test"})

    @pytest.mark.asyncio
    async def test_tables_query_without_context_raises_error(self):
        """Test that tables.query raises error when not authenticated."""
        from bifrost import tables
        from bifrost.client import _clear_client
        from bifrost._context import clear_execution_context

        clear_execution_context()
        _clear_client()

        with pytest.raises(RuntimeError, match="Not logged in"):
            await tables.query("customers")
