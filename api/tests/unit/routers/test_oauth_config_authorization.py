"""Authorization boundaries for platform OAuth SSO configuration."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.oauth_config import (
    MicrosoftOAuthConfigRequest,
    OAuthLoginPreference,
)
from src.routers import oauth_config
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
            "Missing required capability: integrations.read",
        ),
        (
            {"integrations.read"},
            None,
            409,
            "The selected authorization boundary does not match this resource; select platform",
        ),
    ],
)
def test_oauth_config_read_requires_integrations_read_in_platform_boundary(
    capabilities: set[str],
    boundary: AuthorizationBoundary | None,
    status_code: int,
    detail: str,
) -> None:
    authorization = _authorization(capabilities=capabilities, boundary=boundary)

    with pytest.raises(HTTPException) as exc:
        oauth_config._require_platform_oauth_config(
            authorization,
            "integrations.read",
        )

    assert exc.value.status_code == status_code
    assert exc.value.detail == detail


def test_oauth_config_write_rejects_managed_collection() -> None:
    authorization = _authorization(
        capabilities={"integrations.readwrite"},
        boundary=AuthorizationBoundary.managed_organizations(),
    )

    with pytest.raises(HTTPException) as exc:
        oauth_config._require_platform_oauth_config(
            authorization,
            "integrations.readwrite",
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "The selected authorization boundary does not match this resource; "
        "select platform"
    )


@pytest.mark.asyncio
async def test_set_login_preference_uses_effective_actor_and_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commits = 0
    updated_by: list[str] = []
    audit_actions: list[str] = []
    authorization = _authorization(
        capabilities={"integrations.readwrite"},
        boundary=AuthorizationBoundary.platform(),
        email="ops@example.com",
    )

    class _DB:
        async def commit(self) -> None:
            nonlocal commits
            commits += 1

    class _Service:
        def __init__(self, db: _DB) -> None:
            self.db = db

        async def set_login_preference(
            self,
            preference: OAuthLoginPreference,
            *,
            updated_by: str,
        ) -> OAuthLoginPreference:
            updated_by_values.append(updated_by)
            return preference

    updated_by_values = updated_by

    async def _emit_audit(db, action, **kwargs):  # noqa: ANN001, ANN003, ANN201
        audit_actions.append(action)

    monkeypatch.setattr(oauth_config, "OAuthConfigService", _Service)
    monkeypatch.setattr(oauth_config, "emit_audit", _emit_audit)

    db = _DB()
    result = await oauth_config.set_oauth_login_preference(
        OAuthLoginPreference(
            auto_redirect_to_sso=False,
            default_sso_provider=None,
        ),
        SimpleNamespace(db=db),
        authorization,
        db,
    )

    assert result.auto_redirect_to_sso is False
    assert updated_by == ["ops@example.com"]
    assert audit_actions == ["oauth_config.login_preference.update"]
    assert commits == 1


@pytest.mark.asyncio
async def test_set_provider_config_requires_integrations_readwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Service:
        def __init__(self, db):  # noqa: ANN001
            pytest.fail("service should not be constructed without capability")

    monkeypatch.setattr(oauth_config, "OAuthConfigService", _Service)

    with pytest.raises(HTTPException) as exc:
        await oauth_config.set_microsoft_config(
            MicrosoftOAuthConfigRequest(
                client_id="client",
                client_secret="secret",
                tenant_id="organizations",
            ),
            SimpleNamespace(db=SimpleNamespace()),
            _authorization(
                capabilities={"integrations.read"},
                boundary=AuthorizationBoundary.platform(),
            ),
            SimpleNamespace(),
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: integrations.readwrite"
