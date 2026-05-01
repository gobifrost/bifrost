"""Smoke tests for ``bifrost tables`` CLI commands.

The tests mock ``BifrostClient.get_instance`` and ``RefResolver`` so no
network or credentials are required.
"""

from __future__ import annotations

import pathlib
import sys
import unittest.mock as mock

import httpx
from click.testing import CliRunner

# Ensure the standalone bifrost package is importable.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from bifrost.commands.tables import tables_group  # noqa: E402


_DUMMY_REQUEST = httpx.Request("GET", "https://bifrost.test/api/tables")


def _fake_response(body: dict, *, status: int = 200) -> httpx.Response:
    """Build an httpx.Response with a request set (required for raise_for_status)."""
    resp = httpx.Response(status, json=body, request=_DUMMY_REQUEST)
    return resp


def _async_identity(value: str):  # type: ignore[no-untyped-def]
    """Return a coroutine that resolves to ``value``."""

    async def _inner():  # type: ignore[no-untyped-def]
        return value

    return _inner()


def _make_mock_client(captured: dict) -> mock.AsyncMock:
    """Return a mock BifrostClient whose async post/patch record calls."""

    async def capturing_post(path, json=None):  # type: ignore[no-untyped-def]
        captured["post_path"] = path
        captured["post_body"] = json
        return _fake_response({"id": "t1", **(json or {})})

    async def capturing_patch(path, json=None):  # type: ignore[no-untyped-def]
        captured["patch_path"] = path
        captured["patch_body"] = json
        return _fake_response({"id": "t1", **(json or {})})

    async def capturing_get(path):  # type: ignore[no-untyped-def]
        return _fake_response({"id": "t1", "name": "t1"})

    client = mock.AsyncMock()
    client.post = capturing_post
    client.patch = capturing_patch
    client.get = capturing_get
    return client


def _invoke_create(args: list[str], captured: dict) -> "CliRunner._Result":  # type: ignore[name-defined]
    client = _make_mock_client(captured)

    with (
        mock.patch("bifrost.client.BifrostClient.get_instance", return_value=client),
        mock.patch(
            "bifrost.refs.RefResolver.resolve",
            new_callable=lambda: lambda self, kind, ref: _async_identity(ref),
        ),
    ):
        runner = CliRunner()
        return runner.invoke(tables_group, ["create", "--name", "mytable", *args])


class TestCreate:
    def test_basic_create_posts_to_api(self) -> None:
        """Creating a table with just --name posts to /api/tables."""
        captured: dict = {}
        result = _invoke_create([], captured)
        assert result.exit_code == 0, result.output
        assert captured["post_path"] == "/api/tables"
        assert captured["post_body"]["name"] == "mytable"
