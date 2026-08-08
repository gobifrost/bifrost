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

    def __init__(
        self,
        *,
        put_body: dict[str, Any],
        get_content: bytes | None = None,
    ) -> None:
        self.api_url = "http://test.local"
        self._access_token = "test-token"
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._put_body = put_body
        self._get_content = get_content

    def _response(self, method: str, path: str, body: Any) -> httpx.Response:
        request = httpx.Request(method, f"{self.api_url}{path}")
        return httpx.Response(200, json=body, request=request)

    async def put(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        self.calls.append(("PUT", path, json))
        self.last_kwargs = kwargs
        return self._response("PUT", path, self._put_body)

    async def get(self, path: str) -> httpx.Response:
        self.calls.append(("GET", path, None))
        if self._get_content is not None:
            request = httpx.Request("GET", f"{self.api_url}{path}")
            return httpx.Response(200, content=self._get_content, request=request)
        return self._response("GET", path, self._put_body)

    async def delete(self, path: str) -> httpx.Response:
        self.calls.append(("DELETE", path, None))
        return self._response("DELETE", path, self._put_body)


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


def test_create_and_update_do_not_expose_raw_bundle_path_flag() -> None:
    runner = CliRunner()

    create = runner.invoke(agents_group, ["create", "--help"])
    update = runner.invoke(agents_group, ["update", "--help"])

    assert create.exit_code == 0
    assert update.exit_code == 0
    assert "--bundle-path" not in create.output
    assert "--bundle-path" not in update.output
    assert "upload-skill" in runner.invoke(agents_group, ["--help"]).output


def test_upload_skill_uses_validated_bundle_endpoint(
    _patch_client,
    tmp_path,
) -> None:
    agent_id = str(uuid4())
    archive = tmp_path / "expense-tracker.skill"
    archive.write_bytes(b"zip-bytes")
    fake = _patch_client(
        _FakeClient(
            put_body={
                "name": "expense-tracker",
                "bundle_path": "skills/expense-tracker",
                "files": ["SKILL.md"],
            }
        )
    )

    result = CliRunner().invoke(
        agents_group,
        ["upload-skill", agent_id, str(archive), "--json"],
    )

    assert result.exit_code == 0, result.output
    assert fake.calls[0][0:2] == (
        "PUT",
        f"/api/agents/{agent_id}/skill/bundle",
    )
    upload = fake.last_kwargs["files"]["file"]
    assert upload == ("expense-tracker.skill", b"zip-bytes", "application/zip")
    assert fake.last_kwargs["timeout"] == 120


def test_download_skill_writes_portable_archive(_patch_client, tmp_path) -> None:
    agent_id = str(uuid4())
    output = tmp_path / "expense-tracker.zip"
    fake = _patch_client(_FakeClient(put_body={}, get_content=b"portable-skill"))

    result = CliRunner().invoke(
        agents_group,
        ["download-skill", agent_id, str(output), "--json"],
    )

    assert result.exit_code == 0, result.output
    assert fake.calls == [
        ("GET", f"/api/agents/{agent_id}/skill/download", None),
    ]
    assert output.read_bytes() == b"portable-skill"


def test_remove_skill_uses_detach_endpoint_without_prompt(_patch_client) -> None:
    agent_id = str(uuid4())
    fake = _patch_client(_FakeClient(put_body={}))

    result = CliRunner().invoke(
        agents_group,
        ["remove-skill", agent_id, "--yes", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert fake.calls == [
        ("DELETE", f"/api/agents/{agent_id}/skill/bundle", None),
    ]
