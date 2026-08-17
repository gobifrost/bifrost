"""CLI contract tests for canonical knowledge search."""

from __future__ import annotations

import pathlib
import sys
import unittest.mock as mock

import httpx
from click.testing import CliRunner

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from bifrost.commands.knowledge import knowledge_group  # noqa: E402


def _invoke(args: list[str], captured: list[dict]):
    async def post(path: str, json=None):  # type: ignore[no-untyped-def]
        captured.append({"path": path, "json": json})
        return httpx.Response(
            200,
            json=[{"namespace": "runbooks", "content": "Reset the service."}],
            request=httpx.Request("POST", f"https://bifrost.test{path}"),
        )

    client = mock.AsyncMock()
    client.post = post
    with mock.patch("bifrost.client.BifrostClient.get_instance", return_value=client):
        return CliRunner().invoke(knowledge_group, args)


def test_search_forwards_complete_rest_contract() -> None:
    captured: list[dict] = []
    result = _invoke(
        [
            "search",
            "restart failed service",
            "--namespace",
            "runbooks",
            "--namespace",
            "policies",
            "--limit",
            "8",
            "--min-score",
            "0.4",
            "--metadata-filter",
            '{"product":"Bifrost"}',
            "--no-fallback",
            "--global",
            "--json",
        ],
        captured,
    )

    assert result.exit_code == 0, result.output
    assert captured == [
        {
            "path": "/api/knowledge/search",
            "json": {
                "query": "restart failed service",
                "namespace": ["runbooks", "policies"],
                "limit": 8,
                "min_score": 0.4,
                "metadata_filter": {"product": "Bifrost"},
                "fallback": False,
                "scope": "global",
            },
        }
    ]
    assert '"namespace": "runbooks"' in result.output


def test_search_rejects_non_object_metadata_filter() -> None:
    result = _invoke(
        ["search", "query", "--metadata-filter", "[]"],
        [],
    )

    assert result.exit_code == 2
    assert "must be a JSON object" in result.output
