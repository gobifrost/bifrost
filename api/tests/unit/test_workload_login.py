"""CLI workload assertion acquisition stays provider-thin and access-only."""

import asyncio

import httpx
import pytest

from bifrost import workload_login


def test_github_acquisition_preserves_action_request_query(monkeypatch) -> None:
    seen: dict[str, str] = {}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **_kwargs):
            seen["url"] = str(url)
            return httpx.Response(200, json={"value": "assertion"}, request=httpx.Request("GET", str(url)))

    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://actions.example/token?api-version=2.0")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "request-token")
    monkeypatch.setattr(workload_login.httpx, "AsyncClient", lambda **_kwargs: Client())

    assert asyncio.run(workload_login.fetch_github_oidc_token("https://bifrost.example")) == "assertion"
    assert "api-version=2.0" in seen["url"]
    assert "audience=https%3A%2F%2Fbifrost.example" in seen["url"]


def test_azure_managed_identity_uses_access_token_response_field(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            seen.update({"url": url, **kwargs})
            return httpx.Response(200, json={"access_token": "azure-assertion"}, request=httpx.Request("GET", str(url)))

    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://127.0.0.1:8080/identity")
    monkeypatch.setenv("IDENTITY_HEADER", "header")
    monkeypatch.setattr(workload_login.httpx, "AsyncClient", lambda **_kwargs: Client())

    assert asyncio.run(workload_login.fetch_azure_managed_identity_token("api://resource", "client-id")) == "azure-assertion"
    assert seen["params"] == {"api-version": "2019-08-01", "resource": "api://resource", "client_id": "client-id"}


def test_exchange_requires_an_access_only_rfc8693_response(monkeypatch) -> None:
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **_kwargs):
            return httpx.Response(
                200,
                json={
                    "access_token": "access",
                    "issued_token_type": workload_login.ACCESS_TOKEN_TYPE,
                    "token_type": "Bearer",
                    "expires_in": 0,
                },
                request=httpx.Request("POST", "https://bifrost.example/token"),
            )

    monkeypatch.setattr(workload_login.httpx, "AsyncClient", lambda **_kwargs: Client())

    with pytest.raises(ValueError, match="invalid workload token response"):
        asyncio.run(
            workload_login.exchange_subject_token(
                "https://bifrost.example", "assertion", workload_login.ACCESS_TOKEN_TYPE
            )
        )
