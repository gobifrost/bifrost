"""Unit tests for the ``bifrost requirements`` CLI group.

The group wraps ``/api/packages/*`` (install / list / remove). These tests
mock :class:`BifrostClient` so we can assert on the URL/body the CLI sends
without needing the full platform stack.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from bifrost import client as bifrost_client_module
from bifrost.commands.requirements import requirements_group


class _FakeClient:
    """Minimal stand-in for BifrostClient used in CLI unit tests."""

    def __init__(self) -> None:
        self.api_url = "http://test.local"
        self._access_token = "test-token"
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._next_response: tuple[int, Any] = (200, {})

    def queue_response(self, status: int, body: Any) -> None:
        self._next_response = (status, body)

    def _build_response(self, method: str, path: str) -> httpx.Response:
        status, body = self._next_response
        request = httpx.Request(method, f"{self.api_url}{path}")
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body, request=request)
        return httpx.Response(status, text=str(body), request=request)

    async def get(self, path: str, **_kwargs) -> httpx.Response:
        self.calls.append(("GET", path, None))
        return self._build_response("GET", path)

    async def post(self, path: str, *, json: dict | None = None, **_kwargs) -> httpx.Response:
        self.calls.append(("POST", path, json))
        return self._build_response("POST", path)

    async def delete(self, path: str, **_kwargs) -> httpx.Response:
        self.calls.append(("DELETE", path, None))
        return self._build_response("DELETE", path)


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    """Replace BifrostClient.get_instance with one returning a FakeClient."""
    fake = _FakeClient()
    monkeypatch.setattr(
        bifrost_client_module.BifrostClient,
        "get_instance",
        classmethod(lambda cls, require_auth=False: fake),
    )
    return fake


def _invoke(args: list[str]):
    return CliRunner().invoke(
        requirements_group, args, standalone_mode=False, catch_exceptions=False
    )


class TestRequirementsInstall:
    def test_install_no_args_warms_cache(self, fake_client: _FakeClient) -> None:
        fake_client.queue_response(200, {"status": "success", "message": "warmed"})
        result = _invoke(["install"])
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [("POST", "/api/packages/install", {})]

    def test_install_bare_package(self, fake_client: _FakeClient) -> None:
        fake_client.queue_response(200, {"status": "success", "message": "ok"})
        result = _invoke(["install", "reportlab"])
        assert result.exit_code == 0, result.output
        method, path, body = fake_client.calls[0]
        assert method == "POST" and path == "/api/packages/install"
        assert body == {"package_name": "reportlab"}

    def test_install_pinned_version(self, fake_client: _FakeClient) -> None:
        fake_client.queue_response(200, {"status": "success", "message": "ok"})
        result = _invoke(["install", "httpx==0.27.0"])
        assert result.exit_code == 0, result.output
        _, _, body = fake_client.calls[0]
        assert body == {"package_name": "httpx", "version": "0.27.0"}

    def test_install_emits_json_when_flag_set(
        self, fake_client: _FakeClient
    ) -> None:
        fake_client.queue_response(
            200, {"status": "success", "message": "ok", "package_name": "reportlab"}
        )
        result = _invoke(["--json", "install", "reportlab"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["package_name"] == "reportlab"


class TestRequirementsList:
    def test_list_hits_packages_endpoint(self, fake_client: _FakeClient) -> None:
        fake_client.queue_response(
            200, {"packages": [{"name": "httpx", "version": "0.27.0"}], "total_count": 1}
        )
        result = _invoke(["list"])
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [("GET", "/api/packages", None)]


class TestRequirementsRemove:
    def test_remove_hits_delete_endpoint(self, fake_client: _FakeClient) -> None:
        fake_client.queue_response(200, {"status": "success"})
        result = _invoke(["remove", "reportlab"])
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [("DELETE", "/api/packages/reportlab", None)]
