"""CLI contract tests for canonical knowledge search."""

from __future__ import annotations

import pathlib
import sys
import unittest.mock as mock
from typing import Any

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


async def _resolve_ref(_self: Any, kind: str, value: str) -> str:
    if kind == "org" and value == "Acme":
        return "11111111-1111-4111-8111-111111111111"
    return value


def _invoke_documents(args: list[str], client: mock.AsyncMock):
    with (
        mock.patch("bifrost.client.BifrostClient.get_instance", return_value=client),
        mock.patch("bifrost.refs.RefResolver.resolve", new=_resolve_ref),
    ):
        return CliRunner().invoke(knowledge_group, args)


def _json_response(
    method: str,
    path: str,
    payload: Any,
    *,
    status: int = 200,
) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request(method, f"https://bifrost.test{path}"),
    )


def test_list_documents_forwards_exact_organization_boundary() -> None:
    client = mock.AsyncMock()
    client.get.return_value = _json_response(
        "GET",
        "/api/knowledge-sources/documents",
        [],
    )

    result = _invoke_documents(
        [
            "list-documents",
            "--organization",
            "Acme",
            "--namespace",
            "runbooks",
            "--json",
        ],
        client,
    )

    assert result.exit_code == 0, result.output
    assert client.get.await_args.args == ("/api/knowledge-sources/documents",)
    assert client.get.await_args.kwargs["params"] == {
        "limit": 100,
        "offset": 0,
        "namespace": "runbooks",
    }
    assert client.get.await_args.kwargs["headers"] == {
        "X-Bifrost-Boundary": "organization:11111111-1111-4111-8111-111111111111"
    }


def test_canonical_list_documents_command_is_registered() -> None:
    client = mock.AsyncMock()
    client.get.return_value = _json_response(
        "GET",
        "/api/knowledge-sources/documents",
        [],
    )

    result = _invoke_documents(["list-documents", "--global", "--json"], client)

    assert result.exit_code == 0, result.output
    assert client.get.await_args.args == ("/api/knowledge-sources/documents",)
    assert client.get.await_args.kwargs["headers"] == {"X-Bifrost-Boundary": "platform"}


def test_create_document_forwards_global_boundary_and_body() -> None:
    client = mock.AsyncMock()
    client.post.return_value = _json_response(
        "POST",
        "/api/knowledge-sources/runbooks/documents",
        {"id": "doc-1", "key": "restart"},
        status=201,
    )

    result = _invoke_documents(
        [
            "create-document",
            "runbooks",
            "--content",
            "Restart the service.",
            "--key",
            "restart",
            "--metadata",
            '{"product":"Bifrost"}',
            "--global",
            "--json",
        ],
        client,
    )

    assert result.exit_code == 0, result.output
    assert client.post.await_args.args == ("/api/knowledge-sources/runbooks/documents",)
    assert client.post.await_args.kwargs["json"] == {
        "content": "Restart the service.",
        "metadata": {"product": "Bifrost"},
        "key": "restart",
    }
    assert client.post.await_args.kwargs["headers"] == {
        "X-Bifrost-Boundary": "platform"
    }
