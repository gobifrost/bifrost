"""Test-stack API with an in-process GitHub provider fixture, never production.

Only App fixture credentials are intercepted. Existing PAT tests retain their
normal HTTP transport. Browser tests replace GitHub's consent pages, while the
real Bifrost endpoints, encryption, database, Redis and Git worker run normally.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from git import Repo

from shared import github_git_app
from src.main import app  # noqa: F401 -- ASGI export used only by docker-compose.test.yml
from src.services import github_api

APP_ID = 314159
INSTALLATION_ID = 2718
REPOSITORY_ID = 1618
REPOSITORY = "thin-git-app/workspace"
INSTALLATION_TOKEN = "fixture-git-app-installation"
USER_TOKEN = "fixture-git-app-user"
_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_pem = _key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
_converted_codes: set[str] = set()


def _provider(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    assert request.url.host in {"api.github.com", "github.com"}
    if path.startswith("/app-manifests/fixture-manifest-") and path.endswith("/conversions") and request.method == "POST":
        if path in _converted_codes:
            return httpx.Response(409, json={"message": "fixture code already consumed"})
        _converted_codes.add(path)
        return httpx.Response(201, json={"id": APP_ID, "client_id": "fixture-client", "client_secret": "fixture-secret",
            "pem": _pem, "slug": "fixture-git-app", "name": "Bifrost Fixture App", "webhook_secret": "unused"})
    if path.startswith("/app-manifests/"):
        return httpx.Response(422, json={"message": "unknown fixture registration code"})
    if path == "/login/oauth/access_token":
        body = parse_qs(request.content.decode())
        assert body["client_id"] == ["fixture-client"] and body["client_secret"] == ["fixture-secret"]
        assert body["code"] == ["fixture-oauth-code"]
        return httpx.Response(200, json={"access_token": USER_TOKEN})
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if path.startswith("/app"):
        claims = jwt.decode(token, _key.public_key(), algorithms=["RS256"])
        assert claims["iss"] == str(APP_ID)
    else:
        assert token in {USER_TOKEN, INSTALLATION_TOKEN}
    if path == "/app":
        return httpx.Response(200, json={"id": APP_ID, "slug": "fixture-git-app", "name": "Bifrost Fixture App", "permissions": {"contents": "write"}})
    if path == "/user/installations":
        return httpx.Response(200, json={"installations": [{"id": INSTALLATION_ID, "app_id": APP_ID, "account": {"login": "thin-git-app"}}]})
    if path == f"/app/installations/{INSTALLATION_ID}":
        return httpx.Response(200, json={"id": INSTALLATION_ID, "permissions": {"contents": "write"}})
    repository = {"id": REPOSITORY_ID, "name": "workspace", "full_name": REPOSITORY, "private": True,
                  "default_branch": "main", "html_url": f"https://github.com/{REPOSITORY}"}
    if path in {f"/user/installations/{INSTALLATION_ID}/repositories", "/installation/repositories"}:
        return httpx.Response(200, json={"repositories": [repository]})
    if path == f"/app/installations/{INSTALLATION_ID}/access_tokens":
        assert json.loads(request.content)["repository_ids"] == [REPOSITORY_ID]
        return httpx.Response(201, json={"token": INSTALLATION_TOKEN, "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
    if path == f"/repos/{REPOSITORY}/branches":
        return httpx.Response(200, json=[{"name": "main", "protected": False, "commit": {"sha": "0" * 40}}])
    return httpx.Response(404, json={"message": "unknown fixture operation"})


class _GitTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.external = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request):
        if request.headers.get("Authorization") == f"Bearer {INSTALLATION_TOKEN}":
            return _provider(request)
        return await self.external.handle_async_request(request)

    async def aclose(self):
        await self.external.aclose()


def _app_client():
    return httpx.AsyncClient(transport=httpx.MockTransport(_provider), timeout=20, follow_redirects=False)


def _git_client(timeout):
    return httpx.AsyncClient(transport=_GitTransport(), timeout=timeout)


github_git_app.http_client = _app_client
github_api.http_client = _git_client

# This directory is shared with the existing API/worker Git fixture mount.
_bare_path = Path("/tmp/bifrost/solution-repo-fixtures/thin-github/thin-git-app/workspace.git")
_bare_path.parent.mkdir(parents=True, exist_ok=True)
if not _bare_path.exists():
    Repo.init(_bare_path, bare=True, initial_branch="main")
