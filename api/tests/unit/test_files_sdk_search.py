"""Unit test for the bifrost.files.search SDK method.

Mocks the underlying client so no network is required. The test asserts
the SDK posts to the right path with the right body and returns the
expected shape.
"""

from __future__ import annotations

import pathlib
import sys
import unittest.mock as mock

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from bifrost.files import files  # noqa: E402


_REQUEST = httpx.Request("POST", "https://bifrost.test/api/files/search")


def _fake_response(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body, request=_REQUEST)


_PAGE = {
    "query": "needle", "output_mode": "content",
    "matches": [{"file_path": "a.py", "line": 3, "column": 0, "text": "needle",
                 "context_before": [], "context_after": [],
                 "source": {"kind": "workspace", "editable": True}}],
    "files": [], "returned": 1, "has_more_matches": False, "response_complete": True,
    "next_cursor": None, "guidance": "Complete.", "search_time_ms": 4,
}


def _client(captured: dict) -> mock.AsyncMock:
    async def capturing_request(method, path, json=None):  # type: ignore[no-untyped-def]
        captured.update(method=method, path=path, body=json)
        return _fake_response(_PAGE)

    client = mock.AsyncMock()
    client.engine_request = capturing_request
    return client


@pytest.mark.asyncio
async def test_search_posts_a_small_first_page_by_default() -> None:
    captured: dict = {}
    with mock.patch("bifrost.files.get_client", return_value=_client(captured)):
        result = await files.search("needle")

    assert (captured["method"], captured["path"]) == ("POST", "/api/files/search")
    assert captured["body"] == {
        "query": "needle", "is_regex": False, "case_sensitive": False, "source": "all",
        "output_mode": "content", "context_lines": 1, "limit": 25,
    }
    assert result["matches"][0]["file_path"] == "a.py" and result["response_complete"] is True


@pytest.mark.asyncio
async def test_search_passes_through_options() -> None:
    captured: dict = {}
    with mock.patch("bifrost.files.get_client", return_value=_client(captured)):
        await files.search(
            "f.*o", is_regex=True, case_sensitive=True, include_pattern="*.py", source="solutions",
            solution_id="0f6b1c2e-0000-4000-8000-000000000000", output_mode="files", context_lines=0,
            limit=5, cursor="c1",
        )
    assert captured["body"] == {
        "query": "f.*o", "is_regex": True, "case_sensitive": True, "include_pattern": "*.py",
        "source": "solutions", "solution_id": "0f6b1c2e-0000-4000-8000-000000000000",
        "output_mode": "files", "context_lines": 0, "limit": 5, "cursor": "c1",
    }
