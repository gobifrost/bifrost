"""Authorization gates for Platform GitHub repository routes."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models import GitHubConfigRequest, GitOpRequest, ValidateTokenRequest
from src.routers import github
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    *,
    organization_id: UUID,
    capabilities: set[str],
    boundary: AuthorizationBoundary | None = None,
    email: str = "builder@example.com",
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email=email,
        organization_id=organization_id,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary or AuthorizationBoundary.platform(),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


def test_github_repository_requires_platform_boundary() -> None:
    authorization = _authorization(
        organization_id=uuid4(),
        capabilities={"repository.read"},
        boundary=AuthorizationBoundary.organization(uuid4()),
    )

    with pytest.raises(HTTPException) as exc:
        github._require_platform_repository(authorization, "repository.read")

    assert exc.value.status_code == 409
    assert "Select Global" in exc.value.detail


def test_github_repository_requires_capability() -> None:
    authorization = _authorization(organization_id=uuid4(), capabilities=set())

    with pytest.raises(HTTPException) as exc:
        github._require_platform_repository(authorization, "repository.read")

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: repository.read"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route_name", "args", "capability"),
    [
        ("get_config_endpoint", (), "repository.read"),
        ("get_github_status", (), "repository.read"),
        ("get_repo_status", (), "repository.read"),
        ("list_github_repos", (), "repository.read"),
        ("get_commits", (), "repository.read"),
        (
            "validate_github_token",
            (ValidateTokenRequest(token="ghp_test"),),
            "repository.readwrite",
        ),
        (
            "configure_github",
            (GitHubConfigRequest(repo_url="owner/repo", branch="main"),),
            "repository.readwrite",
        ),
        ("disconnect_github", (), "repository.readwrite"),
        ("git_fetch", (), "repository.readwrite"),
    ],
)
async def test_github_routes_require_declared_capability(
    route_name: str,
    args: tuple[object, ...],
    capability: str,
) -> None:
    authorization = _authorization(organization_id=uuid4(), capabilities=set())
    route = getattr(github, route_name)
    ctx = SimpleNamespace()
    db = SimpleNamespace()

    with pytest.raises(HTTPException) as exc:
        if route_name in {"validate_github_token", "configure_github"}:
            await route(args[0], ctx, authorization, db)
        elif route_name == "git_fetch":
            await route(ctx, authorization, db, GitOpRequest())
        else:
            await route(ctx, authorization, db)

    assert exc.value.status_code == 403
    assert exc.value.detail == f"Missing required capability: {capability}"


@pytest.mark.asyncio
async def test_validate_token_saves_global_config_with_effective_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: dict[str, object] = {}
    audits: list[tuple[str, dict[str, object]]] = []
    authorization = _authorization(
        organization_id=uuid4(),
        capabilities={"repository.readwrite"},
        email="ops@example.com",
    )

    class _GitHubClient:
        def __init__(self, token: str) -> None:
            assert token == "ghp_test"

        async def list_repositories(self):  # noqa: ANN201
            return []

    async def _save_github_config(**kwargs):  # noqa: ANN003, ANN201
        saved.update(kwargs)

    async def _emit_audit(db, action, **kwargs):  # noqa: ANN001, ANN003, ANN201
        audits.append((action, kwargs))

    monkeypatch.setattr(github, "GitHubAPIClient", _GitHubClient)
    monkeypatch.setattr(github, "save_github_config", _save_github_config)
    monkeypatch.setattr(github, "emit_audit", _emit_audit)

    await github.validate_github_token(
        ValidateTokenRequest(token="ghp_test"),
        SimpleNamespace(),
        authorization,
        SimpleNamespace(),
    )

    assert saved["org_id"] is None
    assert saved["updated_by"] == "ops@example.com"
    assert audits[0][0] == "github.token.validate"


@pytest.mark.asyncio
async def test_git_fetch_queues_platform_job_with_effective_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: dict[str, object] = {}
    audits: list[tuple[str, dict[str, object]]] = []
    authorization = _authorization(
        organization_id=uuid4(),
        capabilities={"repository.readwrite"},
        email="ops@example.com",
    )

    async def _get_github_config(db, org_id):  # noqa: ANN001, ANN201
        assert org_id is None
        return SimpleNamespace(token="ghp_test", repo_url="https://github.com/o/r")

    async def _publish_git_operation(**kwargs):  # noqa: ANN003, ANN201
        published.update(kwargs)
        return "job-1"

    async def _emit_audit(db, action, **kwargs):  # noqa: ANN001, ANN003, ANN201
        audits.append((action, kwargs))

    monkeypatch.setattr(github, "get_github_config", _get_github_config)
    monkeypatch.setattr(github, "publish_git_operation", _publish_git_operation)
    monkeypatch.setattr(github, "emit_audit", _emit_audit)

    result = await github.git_fetch(
        SimpleNamespace(),
        authorization,
        SimpleNamespace(),
        GitOpRequest(job_id="job-1"),
    )

    assert result.job_id == "job-1"
    assert published["org_id"] == ""
    assert published["user_id"] == str(authorization.effective_actor.user_id)
    assert published["user_email"] == "ops@example.com"
    assert audits[0][0] == "github.git_fetch.queue"
