"""Fixed-host GitHub transport with replica-wide coalescing and quota control."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import ssl
import time
from typing import Any, Awaitable, Callable, cast
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from cryptography.fernet import InvalidToken
from redis.exceptions import LockError

from shared.github_app_auth import GitHubAppError, issue_app_jwt
from src.core.cache.redis_client import get_redis
from src.core.security import decrypt_secret, encrypt_secret

API_URL = "https://api.github.com"
MAX_PAGES = 50


@dataclass(frozen=True)
class AppCredentials:
    app_id: int
    generation: str
    private_key: str = field(repr=False)


@dataclass(frozen=True)
class DeliveryPage:
    entries: list[dict[str, Any]]
    next_cursor: str | None


def seal_cache(scope: str, value: Any, ttl: int) -> str:
    return encrypt_secret(json.dumps({"scope": scope, "value": value, "expires_at": time.time() + ttl}))


def open_cache(scope: str, sealed: str) -> Any:
    # Bind ciphertext to its exact App/installation/generation/permissions
    # context and lifetime. Redis TTLs/keys alone are not authority.
    try:
        entry = json.loads(decrypt_secret(sealed))
        if not isinstance(entry, dict) or entry.get("scope") != scope or type(entry.get("expires_at")) not in {int, float}:
            raise ValueError("Invalid cache context")
        return entry["value"] if entry["expires_at"] > time.time() else None
    except (ValueError, TypeError, KeyError, InvalidToken):
        raise GitHubAppError("cache", "GitHub App cache could not be verified", 503) from None


def retry_delay(headers: httpx.Headers, *, now: float | None = None) -> int:
    current = time.time() if now is None else now
    try:
        retry = float(headers.get("retry-after", "0"))
        reset = float(headers.get("x-ratelimit-reset", "0")) - current
        return max(1, min(86400, math.ceil(max(retry, reset if headers.get("x-ratelimit-remaining") == "0" else 0, 60))))
    except (ValueError, OverflowError):
        return 60


class GitHubAppClient:
    def __init__(self, credentials: AppCredentials, authorize: Callable[[], Awaitable[None]] | None = None):
        self.credentials = credentials
        self.authorize = authorize

    async def request(self, method: str, path: str, *, token: str | None = None,
                      installation_id: int | None = None, data: dict[str, Any] | None = None,
                      delivery_page: bool = False) -> Any:
        # Paths are code-owned. Never follow a provider/user-supplied URL with auth.
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise ValueError("GitHub request path must be relative")
        # JWT administration and installation bearer requests have distinct
        # coordination lanes. Token rotation never creates a new quota lane.
        budget = f"github-app:budget:{self.credentials.app_id}:{installation_id if token else 'app'}"
        async with get_redis() as redis:
            try:
                async with redis.lock(budget + ":lock", timeout=25, blocking_timeout=2):
                    if self.authorize:
                        await self.authorize()
                    remaining = await redis.ttl(budget + ":cooldown")
                    if remaining > 0:
                        raise GitHubAppError("rate_limit", "GitHub requests are waiting for quota recovery", 429, remaining)
                    bearer = token or issue_app_jwt(self.credentials.app_id, self.credentials.private_key)
                    try:
                        async with asyncio.timeout(15):
                            async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False,
                                                         verify=ssl.create_default_context()) as client:
                                response = await client.request(method, API_URL + path, json=data, headers={
                                    "Authorization": f"Bearer {bearer}",
                                    "Accept": "application/vnd.github+json",
                                    "X-GitHub-Api-Version": "2022-11-28",
                                    "User-Agent": "Bifrost-GitHub-App",
                                })
                    except (httpx.HTTPError, TimeoutError):
                        raise GitHubAppError("transport", "GitHub could not be reached", 502) from None
                    if self.authorize:
                        await self.authorize()
                    telemetry = {"status": str(response.status_code)}
                    for name in ("limit", "remaining", "reset"):
                        value = response.headers.get(f"x-ratelimit-{name}", "")
                        if value.isdigit():
                            telemetry[name] = value
                    await cast(Awaitable[int], redis.hset(budget + ":health", mapping=telemetry))
                    await redis.expire(budget + ":health", 3600)
                    if response.headers.get("x-ratelimit-remaining") == "0":
                        await redis.set(budget + ":cooldown", "1", ex=retry_delay(response.headers))
                    try:
                        result = response.json() if response.content else {}
                    except ValueError:
                        raise GitHubAppError("response", "GitHub returned an invalid response", 502) from None
                    secondary = isinstance(result, dict) and "secondary rate limit" in str(result.get("message", "")).lower()
                    if response.status_code == 429 or (response.status_code == 403 and (
                        response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers or secondary
                    )):
                        delay = retry_delay(response.headers)
                        await redis.set(budget + ":cooldown", "1", ex=delay)
                        raise GitHubAppError("rate_limit", "GitHub rate limit reached", 429, delay)
                    if response.status_code in (401, 403, 404):
                        code = {401: "credentials", 403: "permissions", 404: "installation_missing"}[response.status_code]
                        raise GitHubAppError(code, "GitHub denied this App or installation request", 409)
                    if not 200 <= response.status_code < 300:
                        raise GitHubAppError("provider", "GitHub could not complete the request", 502)
                    if delivery_page:
                        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
                            raise GitHubAppError("response", "GitHub returned an invalid delivery history", 502)
                        cursor = None
                        next_link = response.links.get("next", {}).get("url")
                        if next_link:
                            target = urlsplit(next_link)
                            cursors = parse_qs(target.query).get("cursor", [])
                            if target.scheme != "https" or target.netloc != "api.github.com" or target.path != "/app/hook/deliveries" or len(cursors) != 1 or len(cursors[0]) > 1024:
                                raise GitHubAppError("response", "GitHub returned invalid delivery pagination", 502)
                            cursor = cursors[0]
                        return DeliveryPage(result, cursor)
                    return result
            except LockError:
                raise GitHubAppError("busy", "GitHub request capacity is busy", 503, 2) from None

    async def delivery_history(self, cursor: str | None = None) -> DeliveryPage:
        # This endpoint uses opaque Link-header cursors, not numbered pages.
        query = {"per_page": "100"}
        if cursor is not None:
            query["cursor"] = cursor
        return await self.request("GET", "/app/hook/deliveries?" + urlencode(query), delivery_page=True)

    async def installation(self, installation_id: int) -> dict[str, Any]:
        result = await self.request("GET", f"/app/installations/{installation_id}", installation_id=installation_id)
        if not isinstance(result, dict) or result.get("app_id") != self.credentials.app_id or result.get("id") != installation_id:
            raise GitHubAppError("installation", "Installation does not belong to the configured App", 409)
        if result.get("suspended_at"):
            raise GitHubAppError("suspended", "GitHub installation is suspended", 409)
        return result

    async def token(self, installation_id: int, generation: str, *, repository_ids: tuple[int, ...] = (),
                    permissions: dict[str, str] | None = None) -> str:
        requested = permissions or {"metadata": "read"}
        scope = json.dumps([self.credentials.app_id, self.credentials.generation, installation_id,
                            generation, sorted(set(repository_ids)), sorted(requested.items())])
        key = "github-app:token:" + hashlib.sha256(scope.encode()).hexdigest()
        async with get_redis() as redis:
            try:
                async with redis.lock(key + ":lock", timeout=25, blocking_timeout=3):
                    cached = await redis.get(key)
                    if cached:
                        value = open_cache(scope, cached)
                        if value is not None:
                            if not isinstance(value, str):
                                raise GitHubAppError("cache", "GitHub App token cache could not be verified", 503)
                            return value
                    body: dict[str, Any] = {"permissions": requested}
                    if repository_ids:
                        body["repository_ids"] = sorted(set(repository_ids))
                    result = await self.request("POST", f"/app/installations/{installation_id}/access_tokens",
                                                installation_id=installation_id, data=body)
                    if not isinstance(result, dict) or not isinstance(result.get("token"), str):
                        raise GitHubAppError("response", "GitHub did not return an installation token", 502)
                    try:
                        expires = datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00"))
                        ttl = min(3540, int((expires - datetime.now(timezone.utc)).total_seconds()) - 60)
                    except (KeyError, TypeError, ValueError):
                        raise GitHubAppError("response", "GitHub returned an invalid token expiry", 502) from None
                    if ttl <= 0 or result.get("permissions") != requested:
                        raise GitHubAppError("token_scope", "GitHub returned an expired or differently scoped token", 409)
                    if repository_ids and {repo.get("id") for repo in result.get("repositories", []) if isinstance(repo, dict)} != set(repository_ids):
                        raise GitHubAppError("token_scope", "GitHub returned a differently scoped repository token", 409)
                    # This cache is generation-scoped; the caller must fence the
                    # generation again before using its result or committing data.
                    await redis.set(key, seal_cache(scope, result["token"], ttl), ex=ttl)
                    return result["token"]
            except LockError:
                raise GitHubAppError("busy", "Installation token refresh is busy", 503, 2) from None

    async def repositories(self, installation_id: int, generation: str) -> list[dict[str, Any]]:
        key = f"github-app:inventory:{self.credentials.app_id}:{self.credentials.generation}:{installation_id}:{generation}"
        async with get_redis() as redis:
            try:
                async with redis.lock(key + ":lock", timeout=900, blocking_timeout=2):
                    cached = await redis.get(key)
                    if cached:
                        value = open_cache(key, cached)
                        if value is not None:
                            if not isinstance(value, list):
                                raise GitHubAppError("cache", "GitHub App inventory cache could not be verified", 503)
                            return value
                    bearer = await self.token(installation_id, generation)
                    repositories: list[dict[str, Any]] = []
                    expected_total = None
                    seen_ids: set[int] = set()
                    for page in range(1, MAX_PAGES + 1):
                        result = await self.request("GET", f"/installation/repositories?per_page=100&page={page}",
                                                    token=bearer, installation_id=installation_id)
                        if not isinstance(result, dict) or not isinstance(result.get("repositories"), list):
                            raise GitHubAppError("response", "GitHub returned an invalid repository inventory", 502)
                        total = result.get("total_count")
                        if type(total) is not int or total < 0:
                            raise GitHubAppError("response", "GitHub returned an invalid repository count", 502)
                        if total > MAX_PAGES * 100:
                            raise GitHubAppError("inventory_limit", "Installation exceeds the supported 5,000-repository inventory limit", 409)
                        if expected_total is not None and total != expected_total:
                            raise GitHubAppError("inventory_changed", "Repository inventory changed during verification; refresh again", 409)
                        expected_total = total
                        batch = result["repositories"]
                        for repo in batch:
                            if not isinstance(repo, dict) or not isinstance(repo.get("id"), int) or not isinstance(repo.get("full_name"), str):
                                raise GitHubAppError("response", "GitHub returned an invalid repository", 502)
                            if repo["id"] in seen_ids:
                                raise GitHubAppError("inventory_changed", "Repository inventory changed during verification; refresh again", 409)
                            seen_ids.add(repo["id"])
                            repositories.append({"id": repo["id"], "full_name": repo["full_name"], "private": bool(repo.get("private", True))})
                        if len(batch) < 100 or len(repositories) == expected_total:
                            if len(repositories) != expected_total:
                                raise GitHubAppError("inventory_changed", "Repository inventory changed during verification; refresh again", 409)
                            await redis.set(key, seal_cache(key, repositories, 60), ex=60)
                            return repositories
                    raise GitHubAppError("inventory_limit", "Installation exceeds the supported 5,000-repository inventory limit", 409)
            except LockError:
                raise GitHubAppError("busy", "Installation inventory is being refreshed", 503, 2) from None
