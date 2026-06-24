from __future__ import annotations

import importlib

import pytest

from bifrost._context import clear_execution_context, set_execution_context
from bifrost._execution_context import ExecutionContext, Organization

files_sdk = importlib.import_module("bifrost.files")

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


@pytest.fixture
def capture_file_sdk_urls(monkeypatch):
    captured_urls: list[str] = []

    class FakeResponse:
        status_code = 200
        is_success = True

        def json(self):
            if captured_urls[-1].startswith("/api/files/list"):
                return {"files": []}
            if captured_urls[-1].startswith("/api/files/exists"):
                return {"exists": True}
            if captured_urls[-1].startswith("/api/files/signed-url"):
                return {"url": "https://example.invalid/signed", "path": "finance/abc/x.txt"}
            return {"content": ""}

        def raise_for_status(self):
            return None

    class FakeClient:
        async def post(self, url, json=None):
            captured_urls.append(url)
            return FakeResponse()

    monkeypatch.setattr(files_sdk, "get_client", lambda: FakeClient())
    return captured_urls


@pytest.mark.parametrize(
    ("method_name", "args", "kwargs"),
    [
        ("read", ("x.txt",), {"location": "finance"}),
        ("read_bytes", ("x.bin",), {"location": "finance"}),
        ("write", ("x.txt", "hi"), {"location": "finance"}),
        ("write_bytes", ("x.bin", b"hi"), {"location": "finance"}),
        ("list", ("",), {"location": "finance"}),
        ("delete", ("x.txt",), {"location": "finance"}),
        ("exists", ("x.txt",), {"location": "finance"}),
        ("get_signed_url", ("x.txt",), {"location": "finance", "method": "GET"}),
    ],
)
@pytest.mark.asyncio
async def test_file_sdk_appends_solution_query(method_name, args, kwargs, capture_file_sdk_urls):
    set_execution_context(_make_context(solution_id=SOLUTION_ID))

    await getattr(files_sdk.files, method_name)(*args, **kwargs)

    assert capture_file_sdk_urls
    assert f"solution={SOLUTION_ID}" in capture_file_sdk_urls[0]


@pytest.mark.parametrize(
    ("method_name", "args", "kwargs"),
    [
        ("read", ("x.txt",), {"location": "finance"}),
        ("read_bytes", ("x.bin",), {"location": "finance"}),
        ("write", ("x.txt", "hi"), {"location": "finance"}),
        ("write_bytes", ("x.bin", b"hi"), {"location": "finance"}),
        ("list", ("",), {"location": "finance"}),
        ("delete", ("x.txt",), {"location": "finance"}),
        ("exists", ("x.txt",), {"location": "finance"}),
        ("get_signed_url", ("x.txt",), {"location": "finance", "method": "GET"}),
    ],
)
@pytest.mark.asyncio
async def test_file_sdk_omits_solution_query_without_solution_context(
    method_name,
    args,
    kwargs,
    capture_file_sdk_urls,
):
    set_execution_context(_make_context(solution_id=None))

    await getattr(files_sdk.files, method_name)(*args, **kwargs)

    assert capture_file_sdk_urls
    assert all("solution=" not in url for url in capture_file_sdk_urls)
