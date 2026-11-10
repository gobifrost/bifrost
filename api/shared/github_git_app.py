"""GitHub App setup and credentials for the platform's existing Git connection."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlencode, urlsplit
from uuid import UUID, uuid4

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import GitAppManifestRequest, GitAppSetupResponse
from src.config import get_settings
from src.core.cache.redis_client import get_shared_redis
from src.models.orm.config import SystemConfig
from src.services.github_config import GitHubConfig, _get_fernet, get_github_config, lock_github_config
from src.services.oauth_state_core import (
    StateDecodeError, consume_nonce, decode_state_jwt, encode_state_jwt, remember_nonce,
)

SETUP_TTL = timedelta(minutes=30)
STATE_AUDIENCE = "bifrost-git-app"
_NAME = re.compile(r"[A-Za-z0-9_.-]+\Z")


class _ManifestCodeLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # HTTPX logs request URLs at INFO; manifest conversion codes are secrets.
        record.msg = re.sub(r"(https://api\.github\.com/app-manifests/)[^/\s]+(/conversions)",
                            r"\1[redacted]\2", record.getMessage())
        record.args = ()
        return True


logging.getLogger("httpx").addFilter(_ManifestCodeLogFilter())


def http_client() -> httpx.AsyncClient:
    """Transport seam for provider fixtures; production always uses verified TLS."""
    return httpx.AsyncClient(timeout=20, follow_redirects=False)


def fail(message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


def _json(response: httpx.Response) -> dict:
    try:
        result = response.json()
    except ValueError as exc:
        raise fail("GitHub returned an invalid response. Resume setup after checking GitHub.", 502) from exc
    if not isinstance(result, dict):
        raise fail("GitHub returned an invalid response.", 502)
    return result


def app_jwt(app: dict[str, Any]) -> str:
    try:
        pem = app["private_key"].encode()
        key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
            raise ValueError("invalid key")
        now = int(time.time())
        return jwt.encode({"iss": str(app["id"]), "iat": now - 60, "exp": now + 540}, key, algorithm="RS256")
    except (KeyError, TypeError, ValueError) as exc:
        raise fail("A valid RSA private key of at least 2048 bits is required.") from exc


def external_origin() -> str:
    origin = get_settings().public_url.rstrip("/")
    parsed = urlsplit(origin)
    if not parsed.hostname or (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})) or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise fail("Configure BIFROST_PUBLIC_URL as the external HTTPS origin before setting up GitHub.")
    return origin


async def _request(method: str, path: str, token: str, *, data: dict | None = None, params: dict | None = None) -> dict:
    """Only fixed App endpoints and numeric installation IDs reach this transport."""
    if method not in {"GET", "POST"}:
        raise fail("Invalid GitHub App operation.")
    pattern = (r"/(?:app|user/installations|installation/repositories|app/installations/[1-9][0-9]*|user/installations/[1-9][0-9]*/repositories)"
               if method == "GET" else r"/app/installations/[1-9][0-9]*/access_tokens")
    if not re.fullmatch(pattern, path):
        raise fail("Invalid GitHub App operation.")
    # Keep the endpoint allowlist, then encode input before URL construction.
    url = "https://api.github.com" + quote(path, safe="/")
    async with http_client() as client:
        try:
            response = await client.request(method, url, headers={
                "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }, json=data, params=params)
        except httpx.HTTPError as exc:
            raise fail("GitHub could not be reached. Resume setup after checking GitHub.", 502) from exc
    if response.status_code == 429 or (response.status_code == 403 and (response.headers.get("X-RateLimit-Remaining") == "0" or "Retry-After" in response.headers)):
        retry_after = response.headers.get("Retry-After", "")
        reset_at = response.headers.get("X-RateLimit-Reset", "")
        seconds = int(retry_after) if retry_after.isascii() and retry_after.isdecimal() else (
            int(reset_at) - int(time.time()) if reset_at.isascii() and reset_at.isdecimal() else 60
        )
        raise HTTPException(429, "GitHub rate limit reached. Wait before retrying.", headers={"Retry-After": str(min(3600, max(1, seconds)))})
    if response.status_code in {401, 403, 404, 422}:
        raise fail("GitHub App access was denied. Check installation, repository access and permissions.", 403)
    if not response.is_success:
        raise fail("GitHub App request failed.", 502)
    return _json(response)


async def _row(db: AsyncSession, setup_id: UUID, user_id: str, scope: UUID | None, *, expired_ok: bool = False) -> tuple[SystemConfig, dict]:
    row = (await db.execute(select(SystemConfig).where(
        SystemConfig.category == "github_setup", SystemConfig.key == str(setup_id),
        SystemConfig.organization_id == scope,
    ).execution_options(populate_existing=True))).scalar_one_or_none()
    if row is None or row.created_by != str(user_id):
        raise fail("GitHub setup was not found.", 404)
    payload = json.loads(_get_fernet().decrypt(row.value_bytes or b""))
    if not expired_ok and datetime.fromisoformat(payload["expires_at"]) <= datetime.now(timezone.utc):
        raise fail("GitHub setup expired. Start a new authorization for the saved App.", 410)
    return row, payload


async def _save(db: AsyncSession, row: SystemConfig, payload: dict) -> None:
    row.value_bytes = _get_fernet().encrypt(json.dumps(payload).encode())
    row.value_json = {"stage": payload["stage"]}
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()


async def get_setup(db: AsyncSession, setup_id: UUID, user_id: str, scope: UUID | None) -> GitAppSetupResponse:
    _, payload = await _row(db, setup_id, user_id, scope, expired_ok=True)
    return setup_response(setup_id, payload)


async def latest_setup(db: AsyncSession, user_id: str, scope: UUID | None, purpose: str = "workspace") -> GitAppSetupResponse | None:
    rows = (await db.execute(select(SystemConfig).where(
        SystemConfig.category == "github_setup", SystemConfig.organization_id == scope,
        SystemConfig.created_by == str(user_id),
    ).order_by(SystemConfig.created_at.desc()).limit(50))).scalars().all()
    current = None
    for row in rows:
        _, candidate = await _row(db, UUID(row.key), user_id, scope, expired_ok=True)
        if candidate.get("purpose", "workspace") == purpose:
            current = (row, candidate)
            break
    if current is None:
        return None
    row, payload = current
    if payload["stage"] == "registration":
        # The browser may have registered the App without returning to Bifrost.
        # Project recovery in the UI while allowing the original callback once.
        payload["stage"] = "registration_needs_review"
    return setup_response(UUID(row.key), payload)


def setup_response(setup_id: UUID, payload: dict) -> GitAppSetupResponse:
    app = payload.get("app", {})
    slug = app.get("slug")
    if slug and not _NAME.fullmatch(slug):
        raise fail("GitHub returned an invalid App identity.", 502)
    return GitAppSetupResponse(
        purpose=payload.get("purpose", "workspace"), source_id=payload.get("source_id"), source_organization_id=payload.get("source_scope"),
        setup_id=setup_id, stage=payload["stage"], expires_at=payload["expires_at"],
        app_name=app.get("name"), install_url=f"https://github.com/apps/{slug}/installations/new" if slug else None,
        installations=payload.get("installations", []), repositories=payload.get("repositories", []),
    )


async def _state(setup_id: UUID, user_id: str, scope: UUID | None, step: str) -> str:
    token, nonce = encode_state_jwt(audience=STATE_AUDIENCE, claims={
        "setup_id": str(setup_id), "user_id": str(user_id), "scope": str(scope) if scope else None, "step": step,
    })
    await remember_nonce(nonce, audience=STATE_AUDIENCE)
    return f"{step}.{token}"


def _decode_state(state: str, user_id: str, scope: UUID | None, step: str) -> dict:
    try:
        prefix, token = state.split(".", 1)
        payload = decode_state_jwt(token, audience=STATE_AUDIENCE)
        if prefix != step or payload.get("step") != step or payload.get("user_id") != str(user_id) or payload.get("scope") != (str(scope) if scope else None):
            raise ValueError("wrong caller")
        UUID(payload["setup_id"])
        return payload
    except (StateDecodeError, ValueError, KeyError) as exc:
        raise fail("GitHub callback is invalid, expired or belongs to another session.", 403) from exc


async def start_manifest(db: AsyncSession, request: GitAppManifestRequest, user_id: str, scope: UUID | None) -> dict:
    origin = external_origin()
    setup_id = uuid4()
    config = await get_github_config(db, scope)
    payload = {"stage": "registration", "expires_at": (datetime.now(timezone.utc) + SETUP_TTL).isoformat(),
               "purpose": request.purpose, "workflows": request.workflows if request.purpose == "workspace" else False,
               "prior_generation": config.generation if config else None}
    target_scope = request.organization_id if "organization_id" in request.model_fields_set else scope
    payload["source_scope"] = str(target_scope) if target_scope is not None else None
    row = SystemConfig(category="github_setup", key=str(setup_id), organization_id=scope, created_by=str(user_id))
    db.add(row)
    await _save(db, row, payload)
    state = await _state(setup_id, user_id, scope, "manifest")
    action = "https://github.com/settings/apps/new" if request.owner_type == "user" else f"https://github.com/organizations/{request.owner}/settings/apps/new"
    permissions = {"contents": "read" if request.purpose == "solution" else "write"}
    if payload["workflows"]:
        permissions["workflows"] = "write"
    return {**setup_response(setup_id, payload).model_dump(mode="json"),
            "registration_url": action + "?" + urlencode({"state": state}),
            "manifest": {"name": request.name, "url": origin, "public": request.public,
                         "redirect_url": origin + "/settings/github", "callback_urls": [origin + "/settings/github"],
                         "setup_url": origin + "/settings/github", "request_oauth_on_install": False,
                         "hook_attributes": {"active": False, "url": origin}, "default_events": [], "default_permissions": permissions}}


async def complete_manifest(db: AsyncSession, code: str, state: str, user_id: str, scope: UUID | None) -> GitAppSetupResponse:
    claims = _decode_state(state, user_id, scope, "manifest")
    setup_id = UUID(claims["setup_id"])
    redis = await get_shared_redis()
    async with redis.lock(f"github:setup:{setup_id}", timeout=60, blocking_timeout=2):
        row, payload = await _row(db, setup_id, user_id, scope)
        if payload.get("app"):
            return setup_response(setup_id, payload)
        if payload["stage"] != "registration" or not await consume_nonce(claims["nonce"], audience=STATE_AUDIENCE):
            raise fail("Registration needs review in GitHub before continuing.", 409)
        payload["stage"] = "registration_needs_review"
        await _save(db, row, payload)
        # The one-use code is never retried after an uncertain provider response.
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,1024}", code):
            raise fail("Invalid GitHub registration code.")
        async with http_client() as client:
            try:
                response = await client.post(f"https://api.github.com/app-manifests/{quote(code, safe='')}/conversions", headers={"Accept": "application/vnd.github+json"})
            except httpx.HTTPError as exc:
                raise fail("Registration may have completed. Review the App in GitHub; do not create another App.", 502) from exc
        if not response.is_success:
            raise fail("Registration could not be confirmed. Review the App in GitHub before continuing.", 409)
        result = _json(response)
        app = {"id": result["id"], "client_id": result["client_id"], "client_secret": result["client_secret"],
               "private_key": result["pem"], "slug": result["slug"], "name": result["name"]}
        app_jwt(app)
        payload.update(app=app, stage="authorization")
        await _save(db, row, payload)
        return setup_response(setup_id, payload)


async def existing_app(db: AsyncSession, app: dict, user_id: str, scope: UUID | None, purpose: str = "workspace", *, source_scope: UUID | None = None) -> GitAppSetupResponse:
    identity = await _request("GET", "/app", app_jwt(app))
    if int(identity["id"]) != int(app["id"]):
        raise fail("The private key does not belong to this App.", 403)
    setup_id = uuid4()
    config = await get_github_config(db, scope)
    app.update(slug=identity["slug"], name=identity["name"])
    payload = {"app": app, "purpose": purpose, "stage": "authorization", "workflows": purpose == "workspace" and identity.get("permissions", {}).get("workflows") == "write",
               "expires_at": (datetime.now(timezone.utc) + SETUP_TTL).isoformat(), "prior_generation": config.generation if config else None}
    payload["source_scope"] = str(source_scope) if source_scope is not None else None
    row = SystemConfig(category="github_setup", key=str(setup_id), organization_id=scope, created_by=str(user_id))
    db.add(row)
    await _save(db, row, payload)
    return setup_response(setup_id, payload)


async def authorize_setup(db: AsyncSession, setup_id: UUID, user_id: str, scope: UUID | None) -> str:
    row, payload = await _row(db, setup_id, user_id, scope, expired_ok=True)
    if not payload.get("app"):
        raise fail("Finish App registration or use an existing App first.", 409)
    if payload["stage"] == "connected":
        raise fail("This setup has already connected the App.", 409)
    current = await get_github_config(db, scope)
    payload["prior_generation"] = current.generation if current else None
    payload.update(stage="authorization", expires_at=(datetime.now(timezone.utc) + SETUP_TTL).isoformat())
    redis = await get_shared_redis()
    await redis.delete(f"github:setup-user-token:{setup_id}")
    payload.pop("repositories", None)
    await _save(db, row, payload)
    return "https://github.com/login/oauth/authorize?" + urlencode({
        "client_id": payload["app"]["client_id"], "redirect_uri": external_origin() + "/settings/github",
        "state": await _state(setup_id, user_id, scope, "oauth"),
    })


async def complete_authorization(db: AsyncSession, code: str, state: str, user_id: str, scope: UUID | None) -> GitAppSetupResponse:
    claims = _decode_state(state, user_id, scope, "oauth")
    if not await consume_nonce(claims["nonce"], audience=STATE_AUDIENCE):
        raise fail("Authorization callback has already been used.", 409)
    setup_id = UUID(claims["setup_id"])
    row, payload = await _row(db, setup_id, user_id, scope)
    async with http_client() as client:
        try:
            response = await client.post("https://github.com/login/oauth/access_token", headers={"Accept": "application/json"}, data={
                "client_id": payload["app"]["client_id"], "client_secret": payload["app"]["client_secret"],
                "code": code, "redirect_uri": external_origin() + "/settings/github",
            })
        except httpx.HTTPError as exc:
            raise fail("GitHub authorization could not be confirmed. Authorize again to resume.", 502) from exc
    result = _json(response)
    if not response.is_success or not isinstance(result.get("access_token"), str):
        raise fail("GitHub authorization was denied. Authorize again to resume.", 403)
    redis = await get_shared_redis()
    remaining = int((datetime.fromisoformat(payload["expires_at"]) - datetime.now(timezone.utc)).total_seconds())
    if remaining <= 0:
        raise fail("GitHub setup expired. Authorize again to resume.", 410)
    await redis.setex(f"github:setup-user-token:{setup_id}", remaining,
                     _get_fernet().encrypt(result["access_token"].encode()).decode())
    payload["stage"] = "installation"
    await _save(db, row, payload)
    return await refresh_installations(db, setup_id, user_id, scope)


async def _pages(path: str, token: str, key: str) -> list[dict]:
    entries: list[dict] = []
    for page in range(1, 11):
        result = await _request("GET", path, token, params={"page": page, "per_page": 100})
        batch = result.get(key, [])
        entries.extend(batch)
        if len(batch) < 100:
            return entries
    raise fail("Too many GitHub results. Narrow App repository access before continuing.", 422)


async def _user_token(setup_id: UUID) -> str:
    redis = await get_shared_redis()
    encrypted = await redis.get(f"github:setup-user-token:{setup_id}")
    if not encrypted:
        raise fail("Authorize with GitHub again to continue setup.", 409)
    return _get_fernet().decrypt(encrypted.encode()).decode()


async def refresh_installations(db: AsyncSession, setup_id: UUID, user_id: str, scope: UUID | None) -> GitAppSetupResponse:
    row, payload = await _row(db, setup_id, user_id, scope)
    result = await _pages("/user/installations", await _user_token(setup_id), "installations")
    payload["installations"] = [{"id": item["id"], "account": item["account"]["login"]} for item in result
                                if item["app_id"] == payload["app"]["id"] and not item.get("suspended_at")]
    payload["stage"] = "repository" if payload["installations"] else "installation"
    await _save(db, row, payload)
    return setup_response(setup_id, payload)


async def setup_repositories(db: AsyncSession, setup_id: UUID, installation_id: int, user_id: str, scope: UUID | None) -> GitAppSetupResponse:
    await refresh_installations(db, setup_id, user_id, scope)
    row, payload = await _row(db, setup_id, user_id, scope)
    if installation_id not in {item["id"] for item in payload["installations"]}:
        raise fail("This installation is not accessible to the authorizing user.", 403)
    installation = await _request("GET", f"/app/installations/{installation_id}", app_jwt(payload["app"]))
    contents = installation["permissions"].get("contents")
    allowed = {"read", "write"} if payload.get("purpose") == "solution" else {"write"}
    if installation.get("suspended_at") or contents not in allowed:
        raise fail("The installation does not grant the required Contents access.", 403)
    repos = await _pages(f"/user/installations/{installation_id}/repositories", await _user_token(setup_id), "repositories")
    payload.update(installation_id=installation_id, repositories=[{key: repo[key] for key in ("id", "full_name", "private", "default_branch")} for repo in repos])
    await _save(db, row, payload)
    return setup_response(setup_id, payload)


async def selected_config(db: AsyncSession, setup_id: UUID, installation_id: int, repository_id: int, user_id: str, scope: UUID | None) -> GitHubConfig:
    await setup_repositories(db, setup_id, installation_id, user_id, scope)
    row, payload = await _row(db, setup_id, user_id, scope)
    repository = next((repo for repo in payload["repositories"] if repo["id"] == repository_id), None)
    if repository is None:
        raise fail("This repository is not accessible to the authorizing user.", 403)
    if payload.get("purpose", "workspace") == "workspace":
        _check_connected_repository(await get_github_config(db, scope), "https://github.com/" + repository["full_name"])
    payload.update(repository=repository, stage="ready")
    await _save(db, row, payload)
    config = _selected_config(setup_id, payload, user_id, scope)
    await GitAppCredentials(db, config, contents="read" if payload.get("purpose") == "solution" else "write").get_token()
    return config


def _selected_config(setup_id: UUID, payload: dict, user_id: str, scope: UUID | None) -> GitHubConfig:
    repository = payload["repository"]
    return GitHubConfig(repo_url="https://github.com/" + repository["full_name"], token=None, branch=repository["default_branch"], status="pending",
                         auth_kind="app", generation=str(setup_id), app={**payload["app"], "installation_id": payload["installation_id"],
                         "repository_id": repository["id"], "workflows": payload["workflows"]}, setup_id=setup_id, setup_user_id=str(user_id), scope=scope)


async def pending_config(db: AsyncSession, setup_id: UUID, user_id: str, scope: UUID | None, *, executing: bool = False) -> GitHubConfig:
    """Resolve a verified setup; only admitted PlatformJobs use ``executing``."""
    _, payload = await _row(db, setup_id, user_id, scope, expired_ok=executing)
    if payload.get("purpose", "workspace") != "workspace":
        raise fail("This setup is for Solution repositories.", 409)
    if payload["stage"] != "ready":
        raise fail("Select and verify a repository before connecting.", 409)
    # Selection already verified the user's access. Queued work must use the
    # installation binding, independently of the temporary OAuth user token.
    config = _selected_config(setup_id, payload, user_id, scope)
    config.setup_executing = executing
    await GitAppCredentials(db, config).get_token()
    return config


def _check_connected_repository(current: GitHubConfig | None, repository_url: str | None) -> None:
    if current and current.repo_url and current.repo_url.removesuffix(".git").rstrip("/") != repository_url:
        raise fail("Select the connected repository to migrate authentication, or disconnect before changing repositories.", 409)


async def select_repository(db: AsyncSession, setup_id: UUID, installation_id: int, repository_id: int, user_id: str, scope: UUID | None, updated_by: str) -> GitAppSetupResponse:
    _, previous = await _row(db, setup_id, user_id, scope)
    if previous.get("purpose") == "solution" and previous["stage"] == "connected":
        if previous["installation_id"] != installation_id or previous["repository"]["id"] != repository_id:
            raise fail("This setup has already saved a different Solution repository.", 409)
        return setup_response(setup_id, previous)
    candidate = await selected_config(db, setup_id, installation_id, repository_id, user_id, scope)
    row, payload = await _row(db, setup_id, user_id, scope)
    if payload.get("purpose") == "solution":
        from shared.solution_git_sources import save_verified_source
        target_scope = UUID(payload["source_scope"]) if payload.get("source_scope") else None
        source = await save_verified_source(db, payload, target_scope, updated_by)
        payload.update(source_id=str(source.id), stage="connected")
        payload["app"] = {key: value for key, value in payload["app"].items() if key not in {"private_key", "client_secret"}}
        await _save(db, row, payload)
        redis = await get_shared_redis()
        await redis.delete(f"github:setup-user-token:{setup_id}")
        return setup_response(setup_id, payload)
    current = await get_github_config(db, scope)
    if current and current.repo_url:
        _check_connected_repository(current, candidate.repo_url)
        from src.services.github_api import GitHubAPIClient
        assert candidate.repo_url is not None
        client = GitHubAPIClient(token_provider=GitAppCredentials(db, candidate).get_token, installation_auth=True)
        await client.list_branches(candidate.repo_url.removeprefix("https://github.com/"))
        # Credential migration preserves the existing workspace and Git history.
        await activate(db, candidate, current.branch, updated_by)
    return await get_setup(db, setup_id, user_id, scope)


async def activate(db: AsyncSession, config: GitHubConfig, branch: str, updated_by: str) -> None:
    assert config.setup_id is not None
    row, payload = await _row(db, config.setup_id, config.setup_user_id or "", config.scope, expired_ok=config.setup_executing)
    await lock_github_config(db, config.scope)
    current = await get_github_config(db, config.scope)
    if (current.generation if current else None) != payload["prior_generation"]:
        raise fail("The GitHub connection changed during setup. Review it before connecting.", 409)
    _check_connected_repository(current, config.repo_url)
    from src.services.github_config import save_app_github_config
    await save_app_github_config(db, config, branch, updated_by, commit=False)
    payload["stage"] = "connected"
    payload["app"] = {key: value for key, value in payload["app"].items() if key not in {"private_key", "client_secret"}}
    await _save(db, row, payload)
    redis = await get_shared_redis()
    await redis.delete(f"github:setup-user-token:{config.setup_id}")


class GitAppCredentials:
    def __init__(self, db: AsyncSession, config: GitHubConfig, *, contents: str = "write"):
        self.contents = contents
        self.db, self.config = db, config

    async def _current(self) -> None:
        if self.config.setup_id:
            _, payload = await _row(self.db, self.config.setup_id, self.config.setup_user_id or "", self.config.scope, expired_ok=self.config.setup_executing)
            if payload["stage"] != "ready" or payload["repository"]["id"] != self.config.app["repository_id"]:
                raise fail("The pending GitHub connection changed.", 409)
            if payload.get("purpose", "workspace") == "workspace":
                current = await get_github_config(self.db, self.config.scope)
                if (current.generation if current else None) != payload["prior_generation"]:
                    raise fail("The GitHub connection changed during setup.", 409)
                _check_connected_repository(current, self.config.repo_url)
        else:
            current = await get_github_config(self.db, self.config.scope)
            if not current or current.generation != self.config.generation or current.auth_kind != "app":
                raise fail("The GitHub connection changed. Retry with the current connection.", 409)

    async def get_token(self) -> str:
        await self._current()
        app = self.config.app
        identity = f"{self.config.generation}:{app['installation_id']}:{app['repository_id']}:{self.contents}:{app.get('workflows', False)}"
        cache_key = "github:git-token:" + hashlib.sha256(identity.encode()).hexdigest()
        cooldown_key = f"github:git-cooldown:{app['id']}:{app['installation_id']}"
        redis = await get_shared_redis()
        async with redis.lock(cache_key + ":lock", timeout=45, blocking_timeout=5):
            await self._current()
            if await redis.get(cooldown_key):
                raise fail("GitHub installation is rate limited. Wait before retrying.", 429)
            cached = await redis.get(cache_key)
            if cached:
                return _get_fernet().decrypt(cached.encode()).decode()
            permissions = {"contents": self.contents}
            if self.contents == "write" and app.get("workflows"):
                permissions["workflows"] = "write"
            try:
                result = await _request("POST", f"/app/installations/{app['installation_id']}/access_tokens", app_jwt(app),
                                        data={"repository_ids": [app["repository_id"]], "permissions": permissions})
            except HTTPException as exc:
                if exc.status_code == 429:
                    await redis.setex(cooldown_key, int((exc.headers or {}).get("Retry-After", "60")), "1")
                raise
            ttl = int((datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds()) - 300
            if ttl <= 0:
                raise fail("GitHub returned a token too close to expiry.", 502)
            await self._current()
            await redis.setex(cache_key, ttl, _get_fernet().encrypt(result["token"].encode()).decode())
            return result["token"]


def credential_provider(db: AsyncSession, config: GitHubConfig):
    if config.auth_kind == "app":
        return GitAppCredentials(db, config).get_token

    async def personal_token() -> str:
        current = await get_github_config(db, config.scope)
        if not current or current.auth_kind != "pat" or current.generation != config.generation or not current.token:
            raise fail("The GitHub connection changed. Retry with the current connection.", 409)
        return current.token

    return personal_token
