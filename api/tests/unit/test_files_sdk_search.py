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


@pytest.mark.asyncio
async def test_search_posts_to_endpoint_with_defaults() -> None:
    captured: dict = {}

    async def capturing_post(path, json=None):  # type: ignore[no-untyped-def]
        captured["path"] = path
        captured["body"] = json
        return _fake_response({
            "query": "needle",
            "total_matches": 1,
            "files_searched": 1,
            "results": [
                {
                    "file_path": "a.py",
                    "line": 3,
                    "column": 0,
                    "match_text": "needle",
                    "context_before": None,
                    "context_after": None,
                }
            ],
            "truncated": False,
            "search_time_ms": 4,
        })

    client = mock.AsyncMock()
    client.post = capturing_post

    with mock.patch("bifrost.files.get_client", return_value=client):
        result = await files.search("needle")

    assert captured["path"] == "/api/files/search"
    assert captured["body"]["query"] == "needle"
    assert captured["body"]["case_sensitive"] is False
    assert captured["body"]["is_regex"] is False
    assert captured["body"]["include_pattern"] == "**/*"
    assert captured["body"]["max_results"] == 1000
    assert result["total_matches"] == 1
    assert result["results"][0]["file_path"] == "a.py"


@pytest.mark.asyncio
async def test_search_passes_through_options() -> None:
    captured: dict = {}

    async def capturing_post(path, json=None):  # type: ignore[no-untyped-def]
        captured["body"] = json
        return _fake_response({
            "query": "x",
            "total_matches": 0,
            "files_searched": 0,
            "results": [],
            "truncated": False,
            "search_time_ms": 1,
        })

    client = mock.AsyncMock()
    client.post = capturing_post

    with mock.patch("bifrost.files.get_client", return_value=client):
        await files.search(
            "x",
            case_sensitive=True,
            is_regex=True,
            include_pattern="**/*.py",
            max_results=50,
        )

    assert captured["body"]["case_sensitive"] is True
    assert captured["body"]["is_regex"] is True
    assert captured["body"]["include_pattern"] == "**/*.py"
    assert captured["body"]["max_results"] == 50
