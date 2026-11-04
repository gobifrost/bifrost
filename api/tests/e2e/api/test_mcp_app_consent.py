"""Inbound MCP OAuth needs a deliberate, user-bound decision before any code."""
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest


VERIFIER = "test-code-verifier-long-enough-for-pkce-mcp-consent"
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
URI = "https://app.example/callback?env=production"


def start(client, *, client_name=None):
    body = {"redirect_uris": [URI]}
    if client_name is not None:
        body["client_name"] = client_name
    registration = client.post("/register", json=body)
    assert registration.status_code == 201, registration.text
    client_id = registration.json()["client_id"]
    authorize = client.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": URI,
        "state": "client-state & value", "code_challenge": CHALLENGE, "code_challenge_method": "S256",
    }, follow_redirects=False)
    assert authorize.status_code == 302, authorize.text
    return_to = parse_qs(urlsplit(authorize.headers["location"]).query)["return_to"][0]
    request_id = parse_qs(urlsplit(return_to).query)["request"][0]
    return client_id, request_id


@pytest.mark.e2e
@pytest.mark.parametrize("approved", [True, False])
def test_personal_mcp_consent_binds_code_to_the_approving_user(e2e_client, org1_user, approved):
    client_id, request_id = start(e2e_client)
    path = f"/auth/app-connections/{request_id}"
    preview = e2e_client.get(path, headers=browser_headers(org1_user))
    assert preview.status_code == 200, preview.text
    assert preview.json()["client_name"] == "Unnamed App"
    assert preview.json()["redirect_uri"] == URI
    # Opening the legacy callback is safe even with an authenticated browser.
    opened = e2e_client.get("/mcp/callback", params={"internal_state": request_id}, headers=browser_headers(org1_user), follow_redirects=False)
    assert opened.status_code == 302
    assert "code=" not in opened.headers["location"]
    assert e2e_client.get(path, headers=browser_headers(org1_user)).status_code == 200
    result = e2e_client.post(path, json={"approved": approved, "consent_token": preview.json()["consent_token"]}, headers=browser_headers(org1_user))
    assert result.status_code == 200, result.text
    query = parse_qs(urlsplit(result.json()["redirect_url"]).query)
    assert query["env"] == ["production"]
    assert query["state"] == ["client-state & value"]
    assert e2e_client.get(path, headers=browser_headers(org1_user)).status_code == 404
    if not approved:
        assert query["error"] == ["access_denied"]
        assert "code" not in query
        return
    token = e2e_client.post("/token", data={
        "grant_type": "authorization_code", "code": query["code"][0], "redirect_uri": URI,
        "code_verifier": VERIFIER, "client_id": client_id,
    })
    assert token.status_code == 200, token.text
    identity = e2e_client.get("/auth/me", headers={"Authorization": f"Bearer {token.json()['access_token']}"})
    assert identity.status_code == 200
    assert identity.json()["email"] == org1_user.email


@pytest.mark.e2e
def test_stolen_consent_and_unauthenticated_posts_do_not_approve_another_user(e2e_client, org1_user, org2_user):
    _, request_id = start(e2e_client, client_name="Example Client")
    path = f"/auth/app-connections/{request_id}"
    preview = e2e_client.get(path, headers=browser_headers(org1_user))
    body = {"approved": True, "consent_token": preview.json()["consent_token"]}
    assert e2e_client.post(path, json=body, headers=browser_headers(org2_user)).status_code == 403
    assert e2e_client.post(path, json=body, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert e2e_client.get(path, headers=browser_headers(org1_user)).status_code == 200


@pytest.mark.e2e
def test_unregistered_callback_is_rejected_without_redirecting(e2e_client):
    registration = e2e_client.post("/register", json={"redirect_uris": [URI]}).json()
    response = e2e_client.get("/authorize", params={
        "response_type": "code", "client_id": registration["client_id"],
        "redirect_uri": "https://attacker.example/cb", "state": "client-state",
        "code_challenge": CHALLENGE, "code_challenge_method": "S256",
    }, follow_redirects=False)
    assert response.status_code == 400
    assert "location" not in response.headers
    assert response.json()["error"] == "invalid_request"


def browser_headers(user):
    return {**user.headers, "Cookie": f"refresh_token={user.refresh_token}"}


@pytest.mark.e2e
def test_access_only_bearer_cannot_bootstrap_a_new_refresh_session(e2e_client, org1_user):
    _, request_id = start(e2e_client)
    path = f"/auth/app-connections/{request_id}"
    access_only = {**org1_user.headers, "Cookie": ""}
    assert e2e_client.get(path, headers=access_only).status_code == 403
    preview = e2e_client.get(path, headers=browser_headers(org1_user)).json()
    assert e2e_client.post(path, headers=access_only, json={
        "approved": True, "consent_token": preview["consent_token"],
    }).status_code == 403
    assert e2e_client.get(path, headers=browser_headers(org1_user)).status_code == 200
    device = e2e_client.post("/auth/device/code").json()
    assert e2e_client.post("/auth/device/authorize", headers=access_only, json={
        "user_code": device["user_code"],
    }).status_code == 403
    assert e2e_client.post("/auth/device/token", json={"device_code": device["device_code"]}).json() == {"error": "authorization_pending"}


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_registration", [False, True])
async def test_official_mcp_client_completes_discovery_registration_and_consent(e2e_api_url, org1_user, legacy_registration):
    """Use the shipped MCP SDK; only route its advertised localhost origin to the test API."""
    import httpx2 as httpx
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata, OAuthClientInformationFull

    class LocalAPITransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.delegate = httpx.AsyncHTTPTransport()

        async def handle_async_request(self, request):
            target = httpx.URL(e2e_api_url)
            forwarded = httpx.Request(
                request.method,
                request.url.copy_with(scheme=target.scheme, host=target.host, port=target.port),
                headers=request.headers,
                content=await request.aread(),
            )
            return await self.delegate.handle_async_request(forwarded)

        async def aclose(self):
            await self.delegate.aclose()

    class Storage:
        tokens = None
        client = None

        async def get_tokens(self): return self.tokens
        async def set_tokens(self, tokens): self.tokens = tokens
        async def get_client_info(self): return self.client
        async def set_client_info(self, client_info): self.client = client_info

    storage = Storage()
    sdk_callback = "https://client.example/sdk-callback"
    legacy_id = None
    redis = None
    if legacy_registration:
        from uuid import uuid4
        from src.core.cache import get_shared_redis
        legacy_id = f"legacy-{uuid4()}"
        sdk_callback = "http://127.0.0.1:49152/sdk-callback"
        metadata = {"client_name": "MCP Python SDK", "redirect_uris": ["http://127.0.0.1:40000/sdk-callback"], "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "token_endpoint_auth_method": "none"}
        import json
        redis = await get_shared_redis()
        await redis.setex(f"bifrost:mcp:client:{legacy_id}", 300, json.dumps(metadata))
        storage.client = OAuthClientInformationFull(client_id=legacy_id, **metadata)
    callback = []
    async with httpx.AsyncClient(transport=LocalAPITransport(), headers=browser_headers(org1_user)) as browser:
        async def redirect(url):
            authorization = await browser.get(url, follow_redirects=False)
            assert authorization.status_code == 302, authorization.text
            consent_url = parse_qs(urlsplit(authorization.headers["location"]).query)["return_to"][0]
            request_id = parse_qs(urlsplit(consent_url).query)["request"][0]
            path = f"{e2e_api_url}/auth/app-connections/{request_id}"
            details = await browser.get(path)
            assert details.status_code == 200, details.text
            decision = await browser.post(path, json={"approved": True, "consent_token": details.json()["consent_token"]})
            assert decision.status_code == 200, decision.text
            values = parse_qs(urlsplit(decision.json()["redirect_url"]).query)
            callback.extend([values["code"][0], values["state"][0]])

        async def receive_callback():
            return AuthorizationCodeResult(code=callback[0], state=callback[1])

        metadata_response = await browser.get(f"{e2e_api_url}/.well-known/oauth-protected-resource")
        resource_url = metadata_response.json()["resource"]
        provider = OAuthClientProvider(
            server_url=resource_url,
            client_metadata=OAuthClientMetadata(
                client_name="MCP Python SDK", redirect_uris=[sdk_callback],
                grant_types=["authorization_code", "refresh_token"], response_types=["code"],
                token_endpoint_auth_method="none", scope="mcp:access",
            ),
            storage=storage, redirect_handler=redirect, callback_handler=receive_callback,
        )
        async with httpx.AsyncClient(transport=LocalAPITransport(), auth=provider) as client:
            result = await client.post(resource_url, headers={"Accept": "application/json, text/event-stream"}, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "consent-interop-test", "version": "1.0.0"}},
            })
            assert result.status_code == 200, result.text
        assert storage.client is not None
        assert storage.tokens is not None
        assert storage.tokens.refresh_token

    if legacy_id:
        assert redis is not None
        assert storage.client.client_id == legacy_id
        assert not await redis.exists(f"bifrost:mcp:client:{legacy_id}")
