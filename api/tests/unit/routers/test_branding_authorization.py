"""Authorization boundaries for global platform branding settings."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models import BrandingUpdateRequest
from src.routers import branding
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    *,
    capabilities: set[str],
    boundary: AuthorizationBoundary | None = None,
    email: str = "builder@example.com",
) -> AuthorizationContext:
    organization_id = uuid4()
    principal = UserPrincipal(
        user_id=uuid4(),
        email=email,
        organization_id=organization_id,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary
        or AuthorizationBoundary.organization(organization_id),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


@pytest.mark.parametrize(
    ("capabilities", "boundary", "status_code", "detail"),
    [
        (
            set(),
            AuthorizationBoundary.platform(),
            403,
            "Missing required capability: configs.readwrite",
        ),
        (
            {"configs.readwrite"},
            None,
            409,
            "The selected authorization boundary does not match this resource; select platform",
        ),
        (
            {"configs.readwrite"},
            AuthorizationBoundary.managed_organizations(),
            409,
            "The selected authorization boundary does not match this resource; select platform",
        ),
    ],
)
def test_branding_mutations_require_configs_readwrite_in_platform_boundary(
    capabilities: set[str],
    boundary: AuthorizationBoundary | None,
    status_code: int,
    detail: str,
) -> None:
    authorization = _authorization(capabilities=capabilities, boundary=boundary)

    with pytest.raises(HTTPException) as exc:
        branding._require_platform_branding_write(authorization)

    assert exc.value.status_code == status_code
    assert exc.value.detail == detail


@pytest.mark.asyncio
async def test_update_branding_audits_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commits = 0
    audit_actions: list[str] = []
    set_branding_calls: list[dict[str, object]] = []
    authorization = _authorization(
        capabilities={"configs.readwrite"},
        boundary=AuthorizationBoundary.platform(),
        email="ops@example.com",
    )

    class _DB:
        async def commit(self) -> None:
            nonlocal commits
            commits += 1

    class _Repo:
        def __init__(self, db: _DB) -> None:
            self.db = db

        async def set_branding(self, **kwargs):  # noqa: ANN003, ANN201
            set_branding_calls.append(kwargs)
            return SimpleNamespace(
                application_name=kwargs.get("application_name"),
                primary_color=kwargs.get("primary_color"),
                terminology={},
                square_logo_data=None,
                rectangle_logo_data=None,
            )

    async def _emit_audit(db, action, **kwargs):  # noqa: ANN001, ANN003, ANN201
        audit_actions.append(action)

    import src.repositories.branding as branding_repository

    monkeypatch.setattr(branding_repository, "BrandingRepository", _Repo)
    monkeypatch.setattr(branding, "emit_audit", _emit_audit)

    db = _DB()
    result = await branding.update_branding(
        BrandingUpdateRequest(primary_color="#123456"),
        SimpleNamespace(db=db),
        authorization,
    )

    assert result.primary_color == "#123456"
    assert set_branding_calls == [{"primary_color": "#123456", "terminology": None}]
    assert audit_actions == ["branding.update"]
    assert commits == 1
