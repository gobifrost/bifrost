"""HTTP callback coverage for the OAuth stable-identity and new-link split."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
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
    def __init__(self, state: str, payload: dict | None = None):
        self.values = {
            oauth_state_key(state): json.dumps(
                payload
                if payload is not None
                else {
                    "version": 2,
                    "code_verifier": "verifier",
                    "redirect_uri": "https://app.test/callback",
                    "nonce": "test-nonce",
                }
            )
        }

    async def get(self, key):
        return self.values.get(key)

    async def getdel(self, key):
        return self.values.pop(key, None)

    async def delete(self, key):
        self.values.pop(key, None)

    async def setex(self, key, ttl, value):
        self.values[key] = value


def _id_token_claims(tenant_id: str) -> dict:
    return {"tid": tenant_id}


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
    photo_side_effect: object | None = None,
    state_payload: dict | None = None,
    trust_provider_email: bool = False,
):
    state = "callback-state"
    redis = _Redis(state, state_payload)
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
    monkeypatch.setattr(
        OAuthService, "get_provider_config",
        AsyncMock(return_value={"trust_email_for_linking": trust_provider_email}),
    )
    photo = AsyncMock(side_effect=photo_side_effect)
    monkeypatch.setattr(OAuthService, "find_user_by_oauth", lookup)
    monkeypatch.setattr(OAuthService, "link_oauth_account", AsyncMock())
    monkeypatch.setattr(OAuthService, "apply_microsoft_profile_photo", photo)
    monkeypatch.setattr(oauth_sso, "ensure_user_provisioned", provision)
    monkeypatch.setattr(
        oauth_sso,
        "issue_human_session",
        AsyncMock(return_value=SimpleNamespace(access_token="access-token", refresh_token="refresh-token")),
    )
    monkeypatch.setattr(oauth_sso, "set_auth_cookies", lambda *args: None)
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

    return response, lookup, provision, photo


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_info", "tokens"),
    [
        (
            OAuthUserInfo("microsoft", "consumer-id", "changed@clientco.com", raw_data={"userPrincipalName": "changed@clientco.com"}),
            OAuthTokens("access", id_token_claims=_id_token_claims(MICROSOFT_CONSUMER_TENANT_ID)),
        ),
        (
            OAuthUserInfo("microsoft", "guest-id", "", raw_data={"userPrincipalName": "guest#EXT#@clientco.onmicrosoft.com"}),
            OAuthTokens("access", id_token_claims=_id_token_claims("tenant")),
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

    response, lookup, provision, _photo = await _callback(
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

    response, _, provision, photo = await _callback(
        monkeypatch,
        user_info=user_info,
        tokens=OAuthTokens("access", id_token_claims=_id_token_claims("tenant")),
        existing_user=None,
        provision_result=provisioned_user,
    )

    assert response.status_code == 200
    assert provision.await_args.kwargs["email"] == "attacker@clientco.com"
    saved_user, access_token = photo.await_args.args
    assert saved_user is provisioned_user
    assert access_token == "access"


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
    response, lookup, provision, photo = await _callback(
        monkeypatch,
        user_info=user_info,
        tokens=OAuthTokens("access"),
        existing_user=None,
    )

    assert response.status_code == 400
    lookup.assert_awaited_once_with(provider=user_info.provider, provider_user_id=user_info.provider_user_id)
    provision.assert_not_awaited()
    photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_email_only_blocks_a_new_link(monkeypatch):
    user_info = OAuthUserInfo("oidc", "new-id", "", raw_data={})

    response, lookup, provision, _photo = await _callback(
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

    response, lookup, provision, _photo = await _callback(
        monkeypatch,
        user_info=OAuthUserInfo(provider, "unused", "unused@example.com"),
        tokens=OAuthTokens("access"),
        existing_user=None,
        get_user_info_side_effect=parse_provider_response,
    )

    assert response.status_code == 400
    lookup.assert_not_awaited()
    provision.assert_not_awaited()


@pytest.mark.asyncio
async def test_microsoft_sign_in_loads_the_profile_photo(monkeypatch):
    user = _user()

    response, _, _, photo = await _callback(
        monkeypatch,
        user_info=OAuthUserInfo("microsoft", "microsoft-id", "kim@clientco.com"),
        tokens=OAuthTokens("graph-access-token", id_token_claims=_id_token_claims("tenant")),
        existing_user=user,
    )

    assert response.status_code == 200
    saved_user, access_token = photo.await_args.args
    assert saved_user is user
    assert access_token == "graph-access-token"


@pytest.mark.asyncio
async def test_profile_photo_errors_do_not_fail_microsoft_sign_in(monkeypatch):
    response, _, _, photo = await _callback(
        monkeypatch,
        user_info=OAuthUserInfo("microsoft", "microsoft-id", "kim@clientco.com"),
        tokens=OAuthTokens("graph-access-token", id_token_claims=_id_token_claims("tenant")),
        existing_user=_user(),
        photo_side_effect=RuntimeError("graph down"),
    )

    assert response.status_code == 200
    photo.assert_awaited_once()


@pytest.mark.asyncio
async def test_google_sign_in_does_not_fetch_a_microsoft_photo(monkeypatch):
    response, _, _, photo = await _callback(
        monkeypatch,
        user_info=OAuthUserInfo(
            "google",
            "google-id",
            "kim@clientco.com",
            raw_data={"email_verified": True, "hd": "clientco.com"},
        ),
        tokens=OAuthTokens("access"),
        existing_user=_user(),
    )

    assert response.status_code == 200
    photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_state_without_nonce_asks_the_user_to_retry(monkeypatch):
    response, lookup, provision, _photo = await _callback(
        monkeypatch,
        user_info=OAuthUserInfo("google", "google-id", "kim@clientco.com"),
        tokens=OAuthTokens("access"),
        existing_user=None,
        state_payload={
            "code_verifier": "verifier",
            "redirect_uri": "https://app.test/callback",
        },
    )

    assert response.status_code == 400
    assert "expired" in response.json()["detail"].lower()
    lookup.assert_not_awaited()
    provision.assert_not_awaited()


def _init_client(
    monkeypatch,
    *,
    public_url: str = "http://localhost:8000",
    base_url: str = "http://127.0.0.1:8000",
):
    app = FastAPI()
    app.include_router(oauth_sso.router)

    async def db_dependency():
        yield SimpleNamespace(commit=AsyncMock())

    app.dependency_overrides[get_db] = db_dependency
    monkeypatch.setattr(
        oauth_sso, "get_shared_redis", AsyncMock(return_value=_Redis("init-state"))
    )
    monkeypatch.setattr(
        OAuthService,
        "get_authorization_url",
        AsyncMock(return_value="https://idp.example/authorize"),
    )
    monkeypatch.setattr(
        oauth_sso, "get_settings", lambda: SimpleNamespace(public_url=public_url)
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=base_url
    )


@pytest.mark.asyncio
async def test_oauth_init_accepts_only_the_configured_callback_origin(monkeypatch):
    async with _init_client(
        monkeypatch, public_url="https://bifrost.example.com"
    ) as client:
        accepted = await client.get(
            "/auth/oauth/init/google",
            params={
                "redirect_uri": "https://bifrost.example.com/auth/callback/google"
            },
        )
        assert accepted.status_code == 200

        for redirect_uri in (
            "https://evil.example/auth/callback/google",
            "https://bifrost.example.com/auth/callback/github",
            "https://bifrost.example.com/auth/callback/google?next=x",
            "https://bifrost.example.com/auth/callback/google#fragment",
            "https://user:pass@bifrost.example.com/auth/callback/google",
            "https://bifrost.example.com/auth/callback/google/extra",
            "http://bifrost.example.com/auth/callback/google",
            "https://bifrost.example.com:8443/auth/callback/google",
            "javascript:alert(1)//auth/callback/google",
            "https://[bad/auth/callback/google",
        ):
            rejected = await client.get(
                "/auth/oauth/init/google", params={"redirect_uri": redirect_uri}
            )
            assert rejected.status_code == 400, redirect_uri


@pytest.mark.asyncio
async def test_oauth_init_normalises_equivalent_configured_origins(monkeypatch):
    async with _init_client(
        monkeypatch, public_url="https://BIFROST.example.com"
    ) as client:
        response = await client.get(
            "/auth/oauth/init/google",
            params={
                "redirect_uri": "https://bifrost.example.com:443/auth/callback/google"
            },
        )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_oauth_init_allows_loopback_request_origin_for_loopback_public_url(
    monkeypatch,
):
    async with _init_client(monkeypatch) as client:
        response = await client.get(
            "/auth/oauth/init/google",
            params={"redirect_uri": "http://127.0.0.1:8000/auth/callback/google"},
        )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_oauth_init_does_not_trust_a_hostile_host_header(monkeypatch):
    async with _init_client(
        monkeypatch, base_url="http://attacker.example"
    ) as client:
        response = await client.get(
            "/auth/oauth/init/google",
            params={
                "redirect_uri": "http://attacker.example/auth/callback/google"
            },
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_oauth_init_fails_closed_when_public_url_is_unset_outside_loopback(
    monkeypatch,
):
    async with _init_client(
        monkeypatch, public_url="", base_url="http://attacker.example"
    ) as client:
        response = await client.get(
            "/auth/oauth/init/google",
            params={
                "redirect_uri": "http://attacker.example/auth/callback/google"
            },
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_oauth_init_allows_loopback_request_origin_when_public_url_unset(
    monkeypatch,
):
    async with _init_client(monkeypatch, public_url="") as client:
        response = await client.get(
            "/auth/oauth/init/google",
            params={"redirect_uri": "http://127.0.0.1:8000/auth/callback/google"},
        )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_oauth_init_fails_closed_on_malformed_public_url(monkeypatch):
    async with _init_client(
        monkeypatch, public_url="not a url"
    ) as client:
        response = await client.get(
            "/auth/oauth/init/google",
            params={
                "redirect_uri": "http://127.0.0.1:8000/auth/callback/google"
            },
        )

    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("trusted", [False, True])
async def test_oidc_first_link_requires_claim_or_explicit_provider_trust(monkeypatch, trusted):
    user = _user()
    info = OAuthUserInfo("oidc", "subject", user.email, raw_data={"email_verified": False})
    response, _, provision, _ = await _callback(
        monkeypatch, user_info=info, tokens=OAuthTokens("access"),
        existing_user=None, provision_result=user, trust_provider_email=trusted,
    )
    assert response.status_code == (200 if trusted else 400)
    if trusted:
        provision.assert_awaited_once()
    else:
        provision.assert_not_awaited()
