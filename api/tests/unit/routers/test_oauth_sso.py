"""HTTP callback coverage for the OAuth stable-identity and new-link split."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import FastAPI

from src.core.cache.keys import oauth_state_key
from src.core.database import get_db
from src.routers import oauth_sso
from src.services.oauth_sso import (
    MICROSOFT_CONSUMER_TENANT_ID,
    OAuthService,
    OAuthTokens,
    OAuthUserInfo,
)


class _Redis:
    def __init__(self, state: str):
        self.values = {oauth_state_key(state): json.dumps({"code_verifier": "verifier", "redirect_uri": "https://app.test/callback"})}

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)

    async def setex(self, key, ttl, value):
        self.values[key] = value


def _id_token(tenant_id: str) -> str:
    return jwt.encode({"tid": tenant_id}, "test-signature-is-not-checked-at-sign-in", algorithm="HS256")


def _user(email: str = "kim@clientco.com") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        email=email,
        name="Kim",
        is_active=True,
        is_superuser=False,
        is_external=False,
        organization_id=None,
        identity_kind=None,
        is_registered=False,
        is_verified=False,
        last_login=None,
    )


async def _callback(
    monkeypatch: pytest.MonkeyPatch,
    *,
    user_info: OAuthUserInfo,
    tokens: OAuthTokens,
    existing_user: SimpleNamespace | None,
    provision_result: SimpleNamespace | None = None,
    reject_validation: bool = False,
    get_user_info_side_effect: object | None = None,
):
    state = "callback-state"
    redis = _Redis(state)
    db = SimpleNamespace(commit=AsyncMock())
    provision = AsyncMock(return_value=SimpleNamespace(user=provision_result))
    lookup = AsyncMock(return_value=existing_user)

    async def db_dependency():
        yield db

    app = FastAPI()
    app.include_router(oauth_sso.router)
    app.dependency_overrides[get_db] = db_dependency

    monkeypatch.setattr(oauth_sso, "get_shared_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr(OAuthService, "exchange_code_for_tokens", AsyncMock(return_value=tokens))
    monkeypatch.setattr(
        OAuthService,
        "get_user_info",
        AsyncMock(return_value=user_info, side_effect=get_user_info_side_effect),
    )
    monkeypatch.setattr(OAuthService, "find_user_by_oauth", lookup)
    monkeypatch.setattr(OAuthService, "link_oauth_account", AsyncMock())
    monkeypatch.setattr(oauth_sso, "ensure_user_provisioned", provision)
    monkeypatch.setattr(oauth_sso, "get_user_roles", AsyncMock(return_value=[]))
    monkeypatch.setattr(oauth_sso, "resolve_provider_org_claim", AsyncMock(return_value=False))
    monkeypatch.setattr(oauth_sso, "create_access_token", lambda data: "access-token")
    monkeypatch.setattr(oauth_sso, "create_refresh_token", lambda data: ("refresh-token", "jti"))
    monkeypatch.setattr(oauth_sso, "_set_oauth_auth_cookies", lambda *args: None)
    if reject_validation:
        monkeypatch.setattr(
            OAuthService,
            "validate_email_for_linking",
            MagicMock(side_effect=AssertionError("existing links must bypass email validation")),
        )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bifrost.test") as client:
        response = await client.post(
            "/auth/oauth/callback",
            json={"provider": user_info.provider, "code": "code", "state": state},
        )

    return response, lookup, provision


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_info", "tokens"),
    [
        (
            OAuthUserInfo("microsoft", "consumer-id", "changed@clientco.com", raw_data={"userPrincipalName": "changed@clientco.com"}),
            OAuthTokens("access", id_token=_id_token(MICROSOFT_CONSUMER_TENANT_ID)),
        ),
        (
            OAuthUserInfo("microsoft", "guest-id", "", raw_data={"userPrincipalName": "guest#EXT#@clientco.onmicrosoft.com"}),
            OAuthTokens("access", id_token=_id_token("tenant")),
        ),
        (
            OAuthUserInfo("google", "third-party-id", "changed@clientco.com", raw_data={"email_verified": True}),
            OAuthTokens("access"),
        ),
        (
            OAuthUserInfo("google", "unverified-id", "", raw_data={"email_verified": False}),
            OAuthTokens("access"),
        ),
        (
            OAuthUserInfo("oidc", "oidc-id", "", raw_data={"email_verified": False}),
            OAuthTokens("access"),
        ),
    ],
)
async def test_existing_stable_identity_authenticates_despite_current_unlinkable_email(
    monkeypatch, user_info, tokens
):
    established_user = _user()

    response, lookup, provision = await _callback(
        monkeypatch,
        user_info=user_info,
        tokens=tokens,
        existing_user=established_user,
        reject_validation=True,
    )

    assert response.status_code == 200
    lookup.assert_awaited_once_with(provider=user_info.provider, provider_user_id=user_info.provider_user_id)
    provision.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_microsoft_link_uses_upn_and_never_graph_mail(monkeypatch):
    user_info = OAuthUserInfo(
        "microsoft",
        "microsoft-id",
        "attacker@clientco.com",
        raw_data={"userPrincipalName": "attacker@clientco.com", "mail": "victim@clientco.com"},
    )
    provisioned_user = _user("attacker@clientco.com")

    response, _, provision = await _callback(
        monkeypatch,
        user_info=user_info,
        tokens=OAuthTokens("access", id_token=_id_token("tenant")),
        existing_user=None,
        provision_result=provisioned_user,
    )

    assert response.status_code == 200
    assert provision.await_args.kwargs["email"] == "attacker@clientco.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_info",
    [
        OAuthUserInfo("google", "google-no-hd", "kim@clientco.com", raw_data={"email_verified": True}),
        OAuthUserInfo("google", "google-unverified", "kim@clientco.com", raw_data={"email_verified": False, "hd": "clientco.com"}),
        OAuthUserInfo("oidc", "oidc-unverified", "kim@clientco.com", raw_data={"email_verified": False}),
    ],
)
async def test_new_link_with_untrusted_email_never_provisions(monkeypatch, user_info):
    response, lookup, provision = await _callback(
        monkeypatch,
        user_info=user_info,
        tokens=OAuthTokens("access"),
        existing_user=None,
    )

    assert response.status_code == 400
    lookup.assert_awaited_once_with(provider=user_info.provider, provider_user_id=user_info.provider_user_id)
    provision.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_email_only_blocks_a_new_link(monkeypatch):
    user_info = OAuthUserInfo("oidc", "new-id", "", raw_data={})

    response, lookup, provision = await _callback(
        monkeypatch,
        user_info=user_info,
        tokens=OAuthTokens("access"),
        existing_user=None,
    )

    assert response.status_code == 400
    lookup.assert_awaited_once_with(provider="oidc", provider_user_id="new-id")
    provision.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "claims"),
    [
        ("microsoft", {"id": ""}),
        ("google", {"sub": None, "email": "kim@clientco.com"}),
        ("oidc", {"sub": 42, "email": "kim@clientco.com"}),
    ],
)
async def test_missing_stable_id_cannot_reach_lookup_or_provision(monkeypatch, provider, claims):
    async def parse_provider_response(*_args, **_kwargs):
        return OAuthService(MagicMock())._parse_user_info(provider, claims)

    response, lookup, provision = await _callback(
        monkeypatch,
        user_info=OAuthUserInfo(provider, "unused", "unused@example.com"),
        tokens=OAuthTokens("access"),
        existing_user=None,
        get_user_info_side_effect=parse_provider_response,
    )

    assert response.status_code == 400
    lookup.assert_not_awaited()
    provision.assert_not_awaited()
