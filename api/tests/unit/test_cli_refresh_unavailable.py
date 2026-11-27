"""A platform outage must not be reported as an authentication problem.

Regression cover for the behaviour observed during a brief 503: token refresh
returned ``None`` for any non-200, ``get_instance`` treated that as "no
credentials", and the CLI told the operator to run ``bifrost login`` — advice
that cannot work while the server is down, and misleading the moment it is back.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from click.testing import CliRunner


def _save_expired(isolated_credentials, api_url: str) -> None:
    isolated_credentials.save_credentials(
        api_url,
        "secret-access",
        "secret-refresh",
        (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
    )


def _fake_client(api_url: str, responder):
    class FakeAsyncClient:
        def __init__(self, *, base_url, timeout):
            assert base_url == api_url
            assert timeout == 30.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, path, *, json):
            assert path == "/auth/refresh"
            return responder()

    return FakeAsyncClient


@pytest.mark.parametrize("status", [500, 503, 599])
@pytest.mark.asyncio
async def test_refresh_5xx_raises_unavailable_instead_of_dropping_credentials(
    isolated_credentials, monkeypatch, status
) -> None:
    from bifrost import client as client_mod

    api_url = "https://api.example.com"
    _save_expired(isolated_credentials, api_url)
    monkeypatch.setattr(
        client_mod.httpx,
        "AsyncClient",
        _fake_client(
            api_url,
            lambda: httpx.Response(
                status, request=httpx.Request("POST", f"{api_url}/auth/refresh")
            ),
        ),
    )

    with pytest.raises(client_mod.BifrostUnavailableError) as excinfo:
        await client_mod.refresh_connection_access_token(api_url, "secret-access")

    message = str(excinfo.value)
    assert str(status) in message
    assert "unavailable" in message
    # the credentials were kept, so the operator can simply retry
    assert isolated_credentials.get_credentials(api_url) is not None


@pytest.mark.asyncio
async def test_transport_failure_raises_unavailable(
    isolated_credentials, monkeypatch
) -> None:
    from bifrost import client as client_mod

    api_url = "https://api.example.com"
    _save_expired(isolated_credentials, api_url)

    def _explode():
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(
        client_mod.httpx, "AsyncClient", _fake_client(api_url, _explode)
    )

    with pytest.raises(client_mod.BifrostUnavailableError):
        await client_mod.refresh_connection_access_token(api_url, "secret-access")


@pytest.mark.parametrize("status", [400, 401, 403, 429])
@pytest.mark.asyncio
async def test_refresh_4xx_still_returns_none(
    isolated_credentials, monkeypatch, status
) -> None:
    """A rejected refresh token is genuinely a re-login case; keep that signal."""
    from bifrost import client as client_mod

    api_url = "https://api.example.com"
    _save_expired(isolated_credentials, api_url)
    monkeypatch.setattr(
        client_mod.httpx,
        "AsyncClient",
        _fake_client(
            api_url,
            lambda: httpx.Response(
                status, request=httpx.Request("POST", f"{api_url}/auth/refresh")
            ),
        ),
    )

    assert (
        await client_mod.refresh_connection_access_token(api_url, "secret-access")
        is None
    )


@pytest.mark.parametrize("failure", [503, "connect", "timeout"])
@pytest.mark.parametrize("command", ["push", "sync", "pull", "watch", "git", "api", "tables"])
def test_cli_outage_keeps_credentials_and_reports_retry(
    isolated_credentials, monkeypatch, tmp_path, capsys, failure, command
) -> None:
    from bifrost import cli, client as client_mod
    from bifrost.commands.tables import tables_group

    api_url = "https://api.example.com"
    monkeypatch.chdir(tmp_path)
    _save_expired(isolated_credentials, api_url)
    before = isolated_credentials.get_credentials(api_url)

    def respond():
        if failure == "connect":
            raise httpx.ConnectError("secret-refresh must not be printed")
        if failure == "timeout":
            raise httpx.ReadTimeout("secret-access must not be printed")
        return httpx.Response(
            failure, request=httpx.Request("POST", f"{api_url}/auth/refresh")
        )

    monkeypatch.setattr(client_mod.httpx, "AsyncClient", _fake_client(api_url, respond))

    async def unexpected_login(*_args):
        pytest.fail("An outage must never start interactive login")

    monkeypatch.setattr(client_mod, "login_flow", unexpected_login)
    if command == "tables":
        result = CliRunner().invoke(tables_group, ["list", "--json"])
        code, error = result.exit_code, result.stderr
        payload = json.loads(error)
        assert payload["error"] == "server_unavailable"
        assert "Retry" in payload["hint"]
    else:
        args = {
            "git": ["status"],
            "api": ["GET", "/api/tables"],
        }.get(command, [str(tmp_path)])
        code = getattr(cli, f"handle_{command}")(args)
        error = capsys.readouterr().err

    assert code == 3
    assert "unavailable" in error or "could not reach" in error
    assert "Retry" in error
    assert "Not logged in" not in error
    assert "bifrost login" not in error
    assert "Traceback" not in error
    assert "secret-access" not in error
    assert "secret-refresh" not in error
    assert isolated_credentials.get_credentials(api_url) == before


def test_rejected_refresh_still_starts_login(isolated_credentials, monkeypatch) -> None:
    from bifrost import client as client_mod

    api_url = "https://api.example.com"
    _save_expired(isolated_credentials, api_url)
    monkeypatch.setattr(
        client_mod.httpx,
        "AsyncClient",
        _fake_client(
            api_url,
            lambda: httpx.Response(
                401, request=httpx.Request("POST", f"{api_url}/auth/refresh")
            ),
        ),
    )
    logins = []

    async def login_fails(selected_url):
        logins.append(selected_url)
        return False

    monkeypatch.setattr(client_mod, "login_flow", login_fails)
    with pytest.raises(RuntimeError, match="Not logged in"):
        client_mod.BifrostClient.get_instance(require_auth=True)
    assert logins == [api_url]
