"""Unit tests for ``bifrost agents`` CLI guardrails."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest
from click.testing import CliRunner

from bifrost import client as bifrost_client_module
from bifrost.commands.agents import agents_group


class _FakeClient:
    """Minimal async client for agents CLI unit tests."""

    def __init__(self, *, put_body: dict[str, Any]) -> None:
        self.api_url = "http://test.local"
        self._access_token = "test-token"
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._put_body = put_body

    def _response(self, method: str, path: str, body: Any) -> httpx.Response:
        request = httpx.Request(method, f"{self.api_url}{path}")
        return httpx.Response(200, json=body, request=request)

    async def put(self, path: str, *, json: dict[str, Any] | None = None) -> httpx.Response:
        self.calls.append(("PUT", path, json))
        return self._response("PUT", path, self._put_body)

    async def get(self, path: str) -> httpx.Response:
        self.calls.append(("GET", path, None))
        return self._response("GET", path, self._put_body)


@pytest.fixture
def _patch_client(monkeypatch: pytest.MonkeyPatch):
    def _install(client: _FakeClient) -> _FakeClient:
        monkeypatch.setattr(
            bifrost_client_module.BifrostClient,
            "get_instance",
            classmethod(lambda cls, require_auth=False: client),
        )
        return client

    return _install


def test_update_tool_ids_fails_when_persisted_ids_differ(_patch_client) -> None:
    agent_id = str(uuid4())
    tool_id = str(uuid4())
    fake = _patch_client(
        _FakeClient(
            put_body={
                "id": agent_id,
                "name": "Work",
                "tool_ids": [],
            }
        )
    )

    result = CliRunner().invoke(
        agents_group,
        ["update", agent_id, "--tool-ids", tool_id],
    )

    assert result.exit_code == 1
    assert "tool_ids" in result.output
    assert "did not persist" in result.output
    assert fake.calls[0] == ("PUT", f"/api/agents/{agent_id}", {"tool_ids": [tool_id]})
