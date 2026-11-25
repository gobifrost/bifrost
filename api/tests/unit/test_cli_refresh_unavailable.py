"""A platform outage must not be reported as an authentication problem.

Regression cover for the behaviour observed during a brief 503: token refresh
returned ``None`` for any non-200, ``get_instance`` treated that as "no
credentials", and the CLI told the operator to run ``bifrost login`` — advice
that cannot work while the server is down, and misleading the moment it is back.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest


def _save_expired(isolated_credentials, api_url: str) -> None:
    isolated_credentials.save_credentials(
        api_url,
        "secret-access",
        "secret-refresh",
        (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
    )


def _fake_client(client_mod, api_url: str, responder):
    class FakeAsyncClient:
        def __init__(self, *, base_url, timeout, trust_env):
            assert base_url == api_url
            assert timeout == 30.0
            assert trust_env is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, path, *, json):
            assert path == "/auth/refresh"
            return responder()

    return FakeAsyncClient


@pytest.mark.asyncio
async def test_refresh_5xx_raises_unavailable_instead_of_dropping_credentials(
    isolated_credentials, monkeypatch
) -> None:
    from bifrost import client as client_mod

    api_url = "https://api.example.com"
    _save_expired(isolated_credentials, api_url)
    monkeypatch.setattr(
        client_mod.httpx,
        "AsyncClient",
        _fake_client(
            client_mod,
            api_url,
            lambda: httpx.Response(
                503, request=httpx.Request("POST", f"{api_url}/auth/refresh")
            ),
        ),
    )

    with pytest.raises(client_mod.BifrostUnavailableError) as excinfo:
        await client_mod.refresh_connection_access_token(api_url, "secret-access")

    message = str(excinfo.value)
    assert "503" in message
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
        client_mod.httpx, "AsyncClient", _fake_client(client_mod, api_url, _explode)
    )

    with pytest.raises(client_mod.BifrostUnavailableError):
        await client_mod.refresh_connection_access_token(api_url, "secret-access")


@pytest.mark.asyncio
async def test_refresh_401_still_returns_none(
    isolated_credentials, monkeypatch
) -> None:
    """A rejected refresh token is genuinely a re-login case; keep that signal."""
    from bifrost import client as client_mod

    api_url = "https://api.example.com"
    _save_expired(isolated_credentials, api_url)
    monkeypatch.setattr(
        client_mod.httpx,
        "AsyncClient",
        _fake_client(
            client_mod,
            api_url,
            lambda: httpx.Response(
                401, request=httpx.Request("POST", f"{api_url}/auth/refresh")
            ),
        ),
    )

    assert (
        await client_mod.refresh_connection_access_token(api_url, "secret-access")
        is None
    )
