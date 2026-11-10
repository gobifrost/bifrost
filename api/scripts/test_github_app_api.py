"""Test-stack API with an in-process GitHub provider fixture, never production.

Only App fixture credentials are intercepted. Existing PAT tests retain their
normal HTTP transport. Browser tests replace GitHub's consent pages, while the
real Bifrost endpoints, encryption, database, Redis and Git worker run normally.
"""

from __future__ import annotations

import json
import base64
import shutil
import tempfile
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from git import Repo
from fastapi import APIRouter, Request

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
_issued_tokens: dict[str, int] = {}


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
        assert token in {USER_TOKEN, INSTALLATION_TOKEN} or token in _issued_tokens
    if path == "/app":
        return httpx.Response(200, json={"id": APP_ID, "slug": "fixture-git-app", "name": "Bifrost Fixture App", "permissions": {"contents": "write"}})
    if path == "/user/installations":
        return httpx.Response(200, json={"installations": [{"id": INSTALLATION_ID, "app_id": APP_ID, "account": {"login": "thin-git-app"}}]})
    if path == f"/app/installations/{INSTALLATION_ID}":
        return httpx.Response(200, json={"id": INSTALLATION_ID, "permissions": {"contents": "write"}})
    repositories = [{"id": repo_id, "name": name, "full_name": f"thin-git-app/{name}", "private": True,
                     "default_branch": "main", "html_url": f"https://github.com/thin-git-app/{name}"}
                    for repo_id, name in [(REPOSITORY_ID, "workspace"), (1619, "solutions")]]
    if path == f"/user/installations/{INSTALLATION_ID}/repositories":
        return httpx.Response(200, json={"repositories": repositories})
    if path == "/installation/repositories":
        repo_id = _issued_tokens.get(token, REPOSITORY_ID)
        return httpx.Response(200, json={"repositories": [repo for repo in repositories if repo["id"] == repo_id]})
    if path == f"/app/installations/{INSTALLATION_ID}/access_tokens":
        body = json.loads(request.content)
        assert len(body["repository_ids"]) == 1 and body["repository_ids"][0] in {REPOSITORY_ID, 1619}
        repo_id = body["repository_ids"][0]
        assert body["permissions"]["contents"] in {"read", "write"}
        if repo_id == 1619:
            assert body["permissions"] == {"contents": "read"}
        token = INSTALLATION_TOKEN if repo_id == REPOSITORY_ID else f"{INSTALLATION_TOKEN}-{repo_id}-read"
        _issued_tokens[token] = repo_id
        return httpx.Response(201, json={"token": token, "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
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
fixture_router = APIRouter()


@fixture_router.post("/__fixture/github", include_in_schema=False)
async def job_provider(request: Request):
    """Share the same verified synthetic provider with isolated job processes."""
    body = await request.json()
    result = _provider(httpx.Request(body["method"], body["url"], headers=body["headers"], content=base64.b64decode(body["body"])))
    return {"status": result.status_code, "body": result.json()}


app.state.solution_fixture_slug = None


@fixture_router.post("/__fixture/solution-repository", include_in_schema=False)
async def solution_repository(request: Request):
    """Own one tiny browser-test remote; never overwrite another fixture."""
    body = await request.json()
    path = Path("/tmp/bifrost/solution-repo-fixtures/thin-github/thin-git-app/solutions.git")
    if body["action"] == "delete":
        assert body["slug"] == app.state.solution_fixture_slug and app.state.solution_fixture_slug is not None
        shutil.rmtree(path)
        app.state.solution_fixture_slug = None
        return {"deleted": True}
    assert body["action"] == "create" and not path.exists()
    slug = "private-browser-" + uuid4().hex[:8]
    remote = Repo.init(path, bare=True, initial_branch="main")
    with tempfile.TemporaryDirectory() as directory:
        work = Repo.init(directory, initial_branch="main")
        (Path(directory) / "bifrost.solution.yaml").write_text(f"slug: {slug}\nname: Private Browser Fixture\nversion: 1.0.0\n")
        work.index.add(["bifrost.solution.yaml"])
        work.index.commit("Browser fixture")
        work.git.push(str(path), "main")
        work.close()
    remote.close()
    app.state.solution_fixture_slug = slug
    return {"slug": slug, "repo_url": "https://github.com/thin-git-app/solutions"}


# src.main's root MCP mount catches unmatched paths. Test-only fixture routes
# must precede it, just like the production API and OAuth routes.
app.router.routes[0:0] = fixture_router.routes

# This directory is shared with the existing API/worker Git fixture mount.
_bare_path = Path("/tmp/bifrost/solution-repo-fixtures/thin-github/thin-git-app/workspace.git")
_bare_path.parent.mkdir(parents=True, exist_ok=True)
if not _bare_path.exists():
    Repo.init(_bare_path, bare=True, initial_branch="main")
