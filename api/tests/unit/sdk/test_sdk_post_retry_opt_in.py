"""POST-shaped SDK reads and keyed upserts opt into transport retries."""

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_table_upsert_opts_into_retry(monkeypatch):
    module = importlib.import_module("bifrost.tables")
    response = MagicMock(status_code=200)
    response.json.return_value = {"id": "customer-1", "table_id": "table-1", "data": {"healthy": True}}
    client = MagicMock(post=AsyncMock(return_value=response))
    monkeypatch.setattr(module, "get_client", lambda: client)

    await module.tables.upsert("backups", "customer-1", {"healthy": True}, scope="global")

    client.post.assert_awaited_once_with(
        "/api/tables/backups/documents/upsert?scope=global",
        json={"id": "customer-1", "data": {"healthy": True}},
        retry_transient=True,
    )


@pytest.mark.asyncio
async def test_table_update_opts_into_retry(monkeypatch):
    module = importlib.import_module("bifrost.tables")
    response = MagicMock(status_code=200)
    response.json.return_value = {"id": "customer-1", "table_id": "table-1", "data": {"healthy": True}}
    client = MagicMock(patch=AsyncMock(return_value=response))
    monkeypatch.setattr(module, "get_client", lambda: client)

    await module.tables.update("backups", "customer-1", {"healthy": True}, scope="global")

    client.patch.assert_awaited_once_with(
        "/api/tables/backups/documents/customer-1?scope=global",
        json={"data": {"healthy": True}},
        retry_transient=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "documents", "retryable"),
    [
        ("upsert_batch", [{"id": "customer-1", "data": {"healthy": True}}], True),
        ("upsert_batch", [{"data": {"healthy": True}}], False),
        ("insert_batch", [{"id": "customer-1", "data": {"healthy": True}}], False),
    ],
)
async def test_table_batch_retries_only_keyed_upserts(monkeypatch, method, documents, retryable):
    module = importlib.import_module("bifrost.tables")
    response = MagicMock(status_code=200)
    response.json.return_value = {"inserted": 1, "documents": []}
    client = MagicMock(post=AsyncMock(return_value=response))
    monkeypatch.setattr(module, "get_client", lambda: client)

    await getattr(module.tables, method)("backups", documents, scope="global")

    assert client.post.await_args.kwargs["retry_transient"] is retryable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/sdk/integrations/get", {"name": "HaloPSA", "scope": "global"}),
        ("list_mappings", "/api/sdk/integrations/list_mappings", {"name": "HaloPSA", "scope": "global"}),
        ("get_mapping", "/api/sdk/integrations/get_mapping", {"name": "HaloPSA", "scope": "global", "entity_id": None}),
    ],
)
async def test_integration_reads_opt_into_retry(monkeypatch, method, path, body):
    module = importlib.import_module("bifrost.integrations")
    response = MagicMock(status_code=200)
    response.json.return_value = None
    client = MagicMock(post=AsyncMock(return_value=response))
    monkeypatch.setattr(module, "get_client", lambda: client)

    await getattr(module.integrations, method)("HaloPSA", scope="global")

    client.post.assert_awaited_once_with(path, json=body, retry_transient=True)


@pytest.mark.asyncio
async def test_knowledge_search_opts_into_retry(monkeypatch):
    module = importlib.import_module("bifrost.knowledge")
    response = MagicMock(status_code=200)
    response.json.return_value = []
    client = MagicMock(post=AsyncMock(return_value=response))
    monkeypatch.setattr(module, "get_client", lambda: client)

    assert await module.knowledge.search("backup", scope="global") == []

    assert client.post.await_args.args == ("/api/sdk/knowledge/search",)
    assert client.post.await_args.kwargs["retry_transient"] is True
