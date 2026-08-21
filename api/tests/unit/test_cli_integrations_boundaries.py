"""Authorization-boundary forwarding for Integration CLI mutations."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from click.testing import CliRunner

from bifrost.commands.integrations import integrations_group


def _response(method: str, path: str, payload: dict[str, Any]) -> httpx.Response:
    request = httpx.Request(method, f"https://bifrost.test{path}")
    return httpx.Response(200 if method != "POST" else 201, json=payload, request=request)


async def _resolve_ref(_self: Any, kind: str, value: str) -> str:
    if kind == "integration":
        return str(uuid4()) if value == "Pax8" else value
    if kind == "org":
        return "11111111-1111-4111-8111-111111111111"
    return value


def _client() -> AsyncMock:
    client = AsyncMock()

    async def post(path: str, **kwargs: Any) -> httpx.Response:
        payload = {"id": str(uuid4()), **(kwargs.get("json") or {})}
        return _response("POST", path, payload)

    async def put(path: str, **kwargs: Any) -> httpx.Response:
        payload = {"id": str(uuid4()), **(kwargs.get("json") or {})}
        return _response("PUT", path, payload)

    async def get(path: str, **_kwargs: Any) -> httpx.Response:
        return _response("GET", path, {"id": str(uuid4())})

    client.post.side_effect = post
    client.put.side_effect = put
    client.get.side_effect = get
    return client


def _invoke(client: AsyncMock, args: list[str]):
    with (
        patch("bifrost.client.BifrostClient.get_instance", return_value=client),
        patch("bifrost.refs.RefResolver.resolve", new=_resolve_ref),
    ):
        return CliRunner().invoke(integrations_group, args)


def test_create_definition_forwards_platform_boundary() -> None:
    client = _client()

    result = _invoke(client, ["create", "--name", "Pax8"])

    assert result.exit_code == 0, result.output
    assert client.post.await_args.kwargs["headers"] == {
        "X-Bifrost-Boundary": "platform"
    }


def test_update_definition_forwards_platform_boundary() -> None:
    client = _client()

    result = _invoke(client, ["update", "Pax8", "--name", "Pax8 v2"])

    assert result.exit_code == 0, result.output
    assert client.put.await_args.kwargs["headers"] == {
        "X-Bifrost-Boundary": "platform"
    }


def test_create_mapping_forwards_exact_organization_boundary() -> None:
    client = _client()

    result = _invoke(
        client,
        [
            "create-mapping",
            "Pax8",
            "--organization",
            "Acme",
            "--entity-id",
            "tenant-1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert client.post.await_args.kwargs["headers"] == {
        "X-Bifrost-Boundary": (
            "organization:11111111-1111-4111-8111-111111111111"
        )
    }


def test_update_mapping_forwards_exact_boundary_to_lookup_and_write() -> None:
    client = _client()

    result = _invoke(
        client,
        [
            "update-mapping",
            "Pax8",
            "--organization",
            "Acme",
            "--entity-name",
            "Acme tenant",
        ],
    )

    assert result.exit_code == 0, result.output
    expected = {
        "X-Bifrost-Boundary": (
            "organization:11111111-1111-4111-8111-111111111111"
        )
    }
    assert client.get.await_args.kwargs["headers"] == expected
    assert client.put.await_args.kwargs["headers"] == expected
