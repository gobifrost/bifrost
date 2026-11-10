"""Scoped GitHub App connections used only by platform Solution fetches."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from cryptography.fernet import InvalidToken

from shared.github_git_app import GitAppCredentials, _pages, _request, app_jwt, existing_app, fail
from shared.models import GitAppSetupResponse, SolutionGitSource
from src.models.orm.config import SystemConfig
from src.services.github_config import GitHubConfig, _get_fernet, get_github_config

CATEGORY = "github_solution_source"


def query_scope(value: str | None, home_scope: UUID | None) -> UUID | None:
    if value is None:
        return home_scope
    if value == "GLOBAL":
        return None
    try:
        return UUID(value)
    except ValueError:
        raise fail("Select a valid organization or Global scope.", 422) from None


async def authorize_source_scope(db: AsyncSession, user, scope: UUID | None) -> None:
    from src.services.solutions.authorization import authorize_scope
    from src.models.orm.organizations import Organization
    await authorize_scope(db, user, scope, "solutions.readwrite")
    if scope is not None and await db.get(Organization, scope) is None:
        raise fail("The source organization does not exist.", 404)


def repository_name(url: str) -> str:
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", url)
    if not match or match[2] in {".", ".."}:
        raise fail("Select a canonical GitHub HTTPS repository URL without credentials, query or fragment.")
    return f"{match[1]}/{match[2]}"


def _payload(row: SystemConfig) -> dict:
    if not row.value_bytes:
        raise fail("The Solution GitHub source needs reconfiguration.", 409)
    try:
        return json.loads(_get_fernet().decrypt(row.value_bytes))
    except (InvalidToken, ValueError, TypeError):
        raise fail("The Solution GitHub source needs reconfiguration.", 409) from None


def _public(row: SystemConfig, payload: dict) -> SolutionGitSource:
    return SolutionGitSource(id=row.id, app_name=payload["app"]["name"],
        installation_id=payload["installation_id"], organization_id=row.organization_id,
        enabled=payload["enabled"], repositories=payload["repositories"])


async def load_source(db: AsyncSession, source_id: UUID, scope: UUID | None, *, allow_disabled: bool = False) -> tuple[SystemConfig, dict]:
    row = (await db.execute(select(SystemConfig).where(
        SystemConfig.id == source_id, SystemConfig.category == CATEGORY,
        SystemConfig.organization_id == scope,
    ).execution_options(populate_existing=True))).scalar_one_or_none()
    if row is None:
        raise fail("The Solution GitHub source is not available in this scope.", 404)
    payload = _payload(row)
    if not allow_disabled and not payload["enabled"]:
        raise fail("The Solution GitHub source is disconnected. Reconnect it before fetching.", 409)
    return row, payload


async def list_sources(db: AsyncSession, scope: UUID | None) -> list[SolutionGitSource]:
    rows = (await db.execute(select(SystemConfig).where(
        SystemConfig.category == CATEGORY, SystemConfig.organization_id == scope,
    ).order_by(SystemConfig.created_at))).scalars().all()
    return [_public(row, _payload(row)) for row in rows]


async def save_verified_source(db: AsyncSession, setup: dict, scope: UUID | None, updated_by: str) -> SolutionGitSource:
    """Save only the repository selected through the user-bound App setup."""
    if setup.get("purpose") != "solution" or setup["stage"] != "ready":
        raise fail("Verify a Solution repository before saving a source.", 409)
    repository_name("https://github.com/" + setup["repository"]["full_name"])
    key = f"{setup['app']['id']}:{setup['installation_id']}"
    lock_id = int.from_bytes(hashlib.sha256(f"solution-github:{scope}:{key}".encode()).digest()[:8], signed=True)
    await db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
    row = (await db.execute(select(SystemConfig).where(
        SystemConfig.category == CATEGORY, SystemConfig.key == key, SystemConfig.organization_id == scope,
    ).execution_options(populate_existing=True))).scalar_one_or_none()
    if row is None:
        row = SystemConfig(id=uuid4(), category=CATEGORY, key=key, organization_id=scope, created_by=updated_by)
        db.add(row)
        repositories = []
    else:
        repositories = _payload(row)["repositories"]
    repositories = [repo for repo in repositories if repo["id"] != setup["repository"]["id"]]
    repositories.append(setup["repository"])
    payload = {"app": setup["app"], "installation_id": setup["installation_id"],
               "repositories": repositories, "generation": str(uuid4()), "enabled": True}
    row.value_bytes = _get_fernet().encrypt(json.dumps(payload).encode())
    row.value_json = None
    row.updated_by = updated_by
    await db.flush()
    return _public(row, payload)


async def disable_source(db: AsyncSession, source_id: UUID, scope: UUID | None, updated_by: str) -> None:
    row, payload = await load_source(db, source_id, scope, allow_disabled=True)
    payload.update(enabled=False, generation=str(uuid4()))
    row.value_bytes = _get_fernet().encrypt(json.dumps(payload).encode())
    row.updated_by = updated_by
    await db.commit()


async def reuse_app(db: AsyncSession, source_id: UUID | None, user_id: str, scope: UUID | None, target_scope: UUID | None) -> GitAppSetupResponse:
    if source_id:
        _, payload = await load_source(db, source_id, target_scope, allow_disabled=True)
        app = payload["app"]
    else:
        config = await get_github_config(db, scope)
        if config is None or config.scope != scope or config.auth_kind != "app":
            raise fail("No workspace App is configured in this scope.", 409)
        app = config.app
    # Reuse registration credentials, then require fresh GitHub user consent
    # and repository selection. Never reuse the workspace repository binding.
    return await existing_app(db, {key: value for key, value in copy.deepcopy(app).items()
                                  if key in {"id", "client_id", "private_key", "client_secret"}}, user_id, scope, purpose="solution", source_scope=target_scope)


class SolutionGitCredentials(GitAppCredentials):
    def __init__(self, db: AsyncSession, row: SystemConfig, payload: dict, repository: dict):
        self.source_id = row.id
        self.source_generation = payload["generation"]
        self.repository = repository
        self.repo_url = "https://github.com/" + repository_name("https://github.com/" + repository["full_name"])
        config = GitHubConfig(repo_url=self.repo_url, token=None, branch=repository["default_branch"],
            auth_kind="app", status="connected", generation=f"solution:{row.id}:{self.source_generation}", scope=row.organization_id,
            app={**payload["app"], "installation_id": payload["installation_id"], "repository_id": repository["id"], "workflows": False})
        super().__init__(db, config, contents="read")

    async def _current(self) -> None:
        _, payload = await load_source(self.db, self.source_id, self.config.scope)
        if payload["generation"] != self.source_generation:
            raise fail("The Solution GitHub source changed. Preview it again before fetching.", 409)

    async def verify(self) -> str:
        installation = await _request("GET", f"/app/installations/{self.config.app['installation_id']}", app_jwt(self.config.app))
        if installation.get("suspended_at") or installation.get("permissions", {}).get("contents") not in {"read", "write"}:
            raise fail("The Solution GitHub installation no longer grants Contents read access.", 403)
        token = await self.get_token()
        repos = await _pages("/installation/repositories", token, "repositories")
        if not any(repo.get("id") == self.repository["id"] and repo.get("full_name", "").casefold() == self.repository["full_name"].casefold() for repo in repos):
            raise fail("The Solution repository is no longer available to this installation.", 403)
        await self._current()
        return token


async def resolve_credentials(db: AsyncSession, source_id: UUID, scope: UUID | None, repo_url: str) -> SolutionGitCredentials:
    name = repository_name(repo_url)
    row, payload = await load_source(db, source_id, scope)
    repository = next((repo for repo in payload["repositories"] if repo["full_name"].casefold() == name.casefold()), None)
    if repository is None:
        raise fail("This repository is not authorized by the selected Solution source.", 403)
    return SolutionGitCredentials(db, row, payload, repository)


async def clone_authenticated(db: AsyncSession, source_id: UUID, scope: UUID | None, repo_url: str, dest: Path, ref: str | None) -> None:
    from git import Repo
    from src.services.github_sync import _git_auth_env

    credentials = await resolve_credentials(db, source_id, scope, repo_url)
    token = await credentials.verify()
    env = {**_git_auth_env(token), "GIT_CONFIG_COUNT": "3",
           "GIT_CONFIG_KEY_2": "http.followRedirects", "GIT_CONFIG_VALUE_2": "false"}
    try:
        if ref is None:
            repo = await asyncio.to_thread(Repo.clone_from, credentials.repo_url, str(dest), depth=1, env=env)
        else:
            repo = await asyncio.to_thread(Repo.clone_from, credentials.repo_url, str(dest), depth=1, branch=ref, env=env)
        repo.close()
    except Exception:
        # Git exceptions can include transport details. Report no credential or
        # provider output to the API, job error, or structured logs.
        raise fail("Could not fetch the private Solution repository. Check App access and the selected ref.", 422) from None
    await credentials._current()


async def clone_solution_repository(db: AsyncSession | None, source_id: UUID | None, scope: UUID | None, repo_url: str, dest: Path, ref: str | None) -> None:
    """Explicit public or App-authenticated source selection, with no fallback."""
    if source_id is not None:
        if db is None:
            raise fail("An authenticated database scope is required for the selected GitHub source.", 409)
        await clone_authenticated(db, source_id, scope, repo_url, dest, ref)
    else:
        from src.services.solutions.git_sync import clone_repo_to_dir
        await clone_repo_to_dir(repo_url, dest, ref=ref)
