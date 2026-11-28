"""Thin workload-assertion acquisition helpers for ``bifrost login``."""

from __future__ import annotations

import os

import httpx

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
ID_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"


async def fetch_github_oidc_token(audience: str) -> str:
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not request_token:
        raise ValueError("GitHub Actions OIDC request variables are unavailable")
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            httpx.URL(url).copy_merge_params({"audience": audience}),
            headers={"Authorization": f"Bearer {request_token}"},
        )
        response.raise_for_status()
        value = response.json().get("value")
    if not isinstance(value, str) or not value:
        raise ValueError("GitHub Actions did not return an ID token")
    return value


async def fetch_azure_managed_identity_token(resource: str, client_id: str | None = None) -> str:
    endpoint = os.environ.get("IDENTITY_ENDPOINT")
    identity_header = os.environ.get("IDENTITY_HEADER")
    params = {"api-version": "2019-08-01", "resource": resource}
    if client_id:
        params["client_id"] = client_id
    if endpoint and identity_header:
        url, headers = endpoint, {"X-IDENTITY-HEADER": identity_header, "Metadata": "true"}
    else:
        url, headers = "http://169.254.169.254/metadata/identity/oauth2/token", {"Metadata": "true"}
        params["api-version"] = "2018-02-01"
    # Never send an assertion-bearing metadata request through an ambient proxy.
    async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        access_token = response.json().get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("Azure managed identity did not return an access_token")
    return access_token


async def exchange_subject_token(api_url: str, subject_token: str, subject_token_type: str) -> dict:
    async with httpx.AsyncClient(base_url=api_url.rstrip("/"), timeout=30.0) as client:
        response = await client.post(
            "/token",
            data={
                "grant_type": TOKEN_EXCHANGE_GRANT,
                "subject_token": subject_token,
                "subject_token_type": subject_token_type,
            },
        )
    response.raise_for_status()
    payload = response.json()
    expires_in = payload.get("expires_in") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("access_token"), str)
        or not payload["access_token"]
        or payload.get("issued_token_type") != "urn:ietf:params:oauth:token-type:access_token"
        or payload.get("token_type") != "Bearer"
        or not isinstance(expires_in, int)
        or isinstance(expires_in, bool)
        or not 0 < expires_in <= 3600
        or "refresh_token" in payload
    ):
        raise ValueError("Bifrost token exchange returned an invalid workload token response")
    return payload
