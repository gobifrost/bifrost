"""Live database and Redis coverage for the OAuth SSO callback."""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient as RealAsyncClient, Request as HttpRequest, Response as HttpResponse
from sqlalchemy import delete, select

from src.core.cache import get_shared_redis
from src.core.cache.keys import oauth_state_key, refresh_token_jti_key
from src.core.database import get_db
from src.models import User, UserOAuthAccount
from src.routers import oauth_sso
from src.services.oauth_sso import OAuthService, OAuthTokens

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


class _ProviderHttpClient:
    def __init__(self, response: HttpResponse):
        self.response = response
        self.requests: list[tuple[str, dict[str, str]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url: str, *, headers: dict[str, str]):
        self.requests.append((url, headers))
        return self.response


async def test_linked_entra_id_authenticates_same_local_user_after_upn_change(
    db_session, async_session_factory, monkeypatch
):
    """The permanent Entra object ID wins after a linked user's UPN changes."""
    user = User(
        id=uuid4(),
        email=f"oauth-linked-{uuid4().hex[:8]}@clientco.com",
        name="Kim Braun",
        is_active=True,
        is_superuser=True,
        is_verified=True,
        is_registered=True,
    )
    provider_user_id = f"entra-{uuid4()}"
    db_session.add_all(
        [
            user,
            UserOAuthAccount(
                user_id=user.id,
                provider_id="microsoft",
                provider_user_id=provider_user_id,
                email="kim.old@clientco.com",
            ),
        ]
    )
    await db_session.commit()

    state = f"oauth-sso-{uuid4()}"
    state_key = oauth_state_key(state)
    refresh_key = refresh_token_jti_key(str(user.id), "test-jti")
    redis = await get_shared_redis()
    await redis.setex(
        state_key,
        600,
        json.dumps({"code_verifier": "verifier", "redirect_uri": "https://app.test/callback"}),
    )

    async def db_dependency():
        yield db_session

    app = FastAPI()
    app.include_router(oauth_sso.router)
    app.dependency_overrides[get_db] = db_dependency

    provider_client = _ProviderHttpClient(
        HttpResponse(
            200,
            json={
                "id": provider_user_id,
                "displayName": "Kim Braun",
                "userPrincipalName": "kim.renamed@clientco.com",
                "mail": "attacker@other.example",
            },
            request=HttpRequest("GET", "https://graph.example.test/me"),
        )
    )
    token = OAuthTokens(
        access_token="provider-access-token",
        id_token=jwt.encode({"tid": "work-tenant"}, "test-signature-is-not-checked-at-sign-in", algorithm="HS256"),
    )
    token_claims: dict = {}
    provision = AsyncMock()

    monkeypatch.setattr(
        OAuthService,
        "exchange_code_for_tokens",
        AsyncMock(return_value=token),
    )
    monkeypatch.setattr(
        OAuthService,
        "get_provider_config",
        AsyncMock(return_value={"userinfo_url": "https://graph.example.test/me"}),
    )
    monkeypatch.setattr("src.services.oauth_sso.httpx.AsyncClient", lambda *_args, **_kwargs: provider_client)
    monkeypatch.setattr(oauth_sso, "ensure_user_provisioned", provision)
    monkeypatch.setattr(oauth_sso, "get_user_roles", AsyncMock(return_value=[]))
    monkeypatch.setattr(oauth_sso, "resolve_provider_org_claim", AsyncMock(return_value=False))
    monkeypatch.setattr(oauth_sso, "create_access_token", lambda data: token_claims.update(data) or "access-token")
    monkeypatch.setattr(oauth_sso, "create_refresh_token", lambda data: ("refresh-token", "test-jti"))
    monkeypatch.setattr(oauth_sso, "_set_oauth_auth_cookies", lambda *_args: None)

    try:
        async with RealAsyncClient(transport=ASGITransport(app=app), base_url="http://bifrost.test") as client:
            response = await client.post(
                "/auth/oauth/callback",
                json={"provider": "microsoft", "code": "code", "state": state},
            )

        assert response.status_code == 200
        assert token_claims["sub"] == str(user.id)
        assert token_claims["email"] == user.email
        assert provider_client.requests == [
            ("https://graph.example.test/me", {"Authorization": "Bearer provider-access-token"})
        ]
        provision.assert_not_awaited()

        async with async_session_factory() as verification_session:
            linked_user_id = await verification_session.scalar(
                select(UserOAuthAccount.user_id).where(
                    UserOAuthAccount.provider_id == "microsoft",
                    UserOAuthAccount.provider_user_id == provider_user_id,
                )
            )
        assert linked_user_id == user.id
    finally:
        await redis.delete(state_key, refresh_key)
        await db_session.rollback()
        async with async_session_factory() as cleanup_session:
            await cleanup_session.execute(
                delete(UserOAuthAccount).where(UserOAuthAccount.user_id == user.id)
            )
            await cleanup_session.execute(delete(User).where(User.id == user.id))
            await cleanup_session.commit()
