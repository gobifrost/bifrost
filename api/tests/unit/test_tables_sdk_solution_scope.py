from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from bifrost._context import clear_execution_context, set_execution_context
from bifrost._execution_context import ExecutionContext, Organization

tables_sdk = importlib.import_module("bifrost.tables")

SOLUTION_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _reset_execution_context():
    clear_execution_context()
    yield
    clear_execution_context()


def _make_context(solution_id: str | None) -> ExecutionContext:
    org = Organization(
        id="00000000-0000-0000-0000-000000000000",
        name="Test Org",
    )
    return ExecutionContext(
        user_id="00000000-0000-0000-0000-000000000999",
        email="test@example.com",
        name="Test User",
        scope=org.id,
        organization=org,
        is_platform_admin=False,
        is_function_key=False,
        execution_id="00000000-0000-0000-0000-000000000111",
        workflow_name="wf",
        solution_id=solution_id,
    )


@dataclass
class FakeResponse:
    status_code: int
    payload: dict[str, Any]
    url: str

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def reason_phrase(self) -> str:
        return "Not Found" if self.status_code == 404 else "OK"

    @property
    def request(self) -> httpx.Request:
        return httpx.Request("POST", f"https://bifrost.test{self.url}")

    def json(self) -> dict[str, Any]:
        return self.payload

    def raise_for_status(self) -> None:
        if not self.is_success:
            raise httpx.HTTPStatusError(
                f"{self.status_code} {self.reason_phrase}",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )


class FakeClient:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def post(self, url: str, json: dict[str, Any] | None = None) -> FakeResponse:
        self.urls.append(url)
        if url.startswith("/api/tables/") and len(self.urls) == 1:
            return FakeResponse(404, {"detail": "Table not found"}, url)
        if url.startswith("/api/tables?"):
            return FakeResponse(
                201,
                {
                    "id": "table-id",
                    "name": "customers",
                    "organization_id": None,
                    "created_by": "test@example.com",
                },
                url,
            )
        if url.endswith("/documents/batch") or "/documents/batch?" in url:
            return FakeResponse(
                200,
                {
                    "inserted": 1,
                    "documents": [
                        {"id": "doc-id", "table_id": "table-id", "data": {"name": "Acme"}},
                    ],
                },
                url,
            )
        return FakeResponse(
            200,
            {"id": "doc-id", "table_id": "table-id", "data": {"name": "Acme"}},
            url,
        )


@pytest.mark.parametrize(
    ("method_name", "args", "kwargs"),
    [
        ("insert", ("customers", {"name": "Acme"}), {}),
        ("upsert", ("customers", "doc-id", {"name": "Acme"}), {}),
        ("insert_batch", ("customers", [{"name": "Acme"}]), {}),
        ("upsert_batch", ("customers", [{"id": "doc-id", "data": {"name": "Acme"}}]), {}),
    ],
)
@pytest.mark.asyncio
async def test_solution_context_does_not_auto_create_after_404(
    method_name,
    args,
    kwargs,
    monkeypatch,
):
    client = FakeClient()
    monkeypatch.setattr(tables_sdk, "get_client", lambda: client)
    set_execution_context(_make_context(solution_id=SOLUTION_ID))

    with pytest.raises(httpx.HTTPStatusError, match="404 Not Found"):
        await getattr(tables_sdk.tables, method_name)(*args, **kwargs)

    assert len(client.urls) == 1
    assert "solution=" in client.urls[0]
    assert all(not url.startswith("/api/tables?") for url in client.urls)


@pytest.mark.parametrize(
    ("method_name", "args", "kwargs"),
    [
        ("insert", ("customers", {"name": "Acme"}), {}),
        ("upsert", ("customers", "doc-id", {"name": "Acme"}), {}),
        ("insert_batch", ("customers", [{"name": "Acme"}]), {}),
        ("upsert_batch", ("customers", [{"id": "doc-id", "data": {"name": "Acme"}}]), {}),
    ],
)
@pytest.mark.asyncio
async def test_non_solution_context_still_auto_creates_after_404(
    method_name,
    args,
    kwargs,
    monkeypatch,
):
    client = FakeClient()
    monkeypatch.setattr(tables_sdk, "get_client", lambda: client)
    set_execution_context(_make_context(solution_id=None))

    await getattr(tables_sdk.tables, method_name)(*args, **kwargs)

    assert client.urls[0].startswith("/api/tables/customers/documents")
    assert client.urls[1] == "/api/tables?scope=00000000-0000-0000-0000-000000000000"
    assert client.urls[2] == client.urls[0]


@pytest.mark.asyncio
async def test_solution_context_create_table_fails_before_sdk_create_endpoint(
    monkeypatch,
):
    client = FakeClient()
    monkeypatch.setattr(tables_sdk, "get_client", lambda: client)
    set_execution_context(_make_context(solution_id=SOLUTION_ID))

    with pytest.raises(RuntimeError, match="declare tables in the solution manifest"):
        await tables_sdk.tables.create("customers")

    assert client.urls == []
