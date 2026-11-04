"""Live database and Redis coverage for the OAuth SSO callback."""

import io
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient as RealAsyncClient, Request as HttpRequest, Response as HttpResponse
from PIL import Image
from sqlalchemy import delete, select

from src.core.cache import get_shared_redis
from src.core.cache.keys import oauth_state_key
from src.core.database import get_db
from src.models import User, UserOAuthAccount
from src.routers import oauth_sso
from src.services.oauth_sso import MICROSOFT_GRAPH_PHOTO_URL, OAuthService, OAuthTokens

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), (12, 34, 56)).save(buffer, format="PNG")
    return buffer.getvalue()


class _ProviderHttpClient:
    def __init__(self, userinfo: HttpResponse, photo: HttpResponse):
        self.userinfo = userinfo
        self.photo = photo
        self.requests: list[tuple[str, dict[str, str]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    @asynccontextmanager
    async def stream(self, method: str, url: str, *, headers: dict[str, str]):
        yield await self.get(url, headers=headers)

    async def get(self, url: str, *, headers: dict[str, str]):
        self.requests.append((url, headers))
        if url == MICROSOFT_GRAPH_PHOTO_URL:
            return self.photo
        return self.userinfo


@asynccontextmanager
async def _entra_callback(
    db_session,
    async_session_factory,
    monkeypatch: pytest.MonkeyPatch,
    photo: HttpResponse,
) -> AsyncIterator[tuple[HttpResponse, _ProviderHttpClient, User, AsyncMock, AsyncMock]]:
    """Sign in a linked Entra user through the live callback, then delete the row."""
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
        ),
        photo,
    )
    token = OAuthTokens(
        access_token="provider-access-token",
        id_token=jwt.encode({"tid": "work-tenant"}, "test-signature-is-not-checked-at-sign-in", algorithm="HS256"),
    )

    monkeypatch.setattr(OAuthService, "exchange_code_for_tokens", AsyncMock(return_value=token))
    monkeypatch.setattr(
        OAuthService,
        "get_provider_config",
        AsyncMock(return_value={"userinfo_url": "https://graph.example.test/me"}),
    )
    monkeypatch.setattr("src.services.oauth_sso.httpx.AsyncClient", lambda *_args, **_kwargs: provider_client)
    provision = AsyncMock()
    issue_session = AsyncMock(
        return_value=SimpleNamespace(access_token="access-token", refresh_token="refresh-token")
    )
    monkeypatch.setattr(oauth_sso, "ensure_user_provisioned", provision)
    monkeypatch.setattr(oauth_sso, "issue_human_session", issue_session)
    monkeypatch.setattr(oauth_sso, "set_auth_cookies", lambda *_args: None)

    try:
        async with RealAsyncClient(transport=ASGITransport(app=app), base_url="http://bifrost.test") as client:
            response = await client.post(
                "/auth/oauth/callback",
                json={"provider": "microsoft", "code": "code", "state": state},
            )
        yield response, provider_client, user, issue_session, provision
    finally:
        await redis.delete(state_key)
        await db_session.rollback()
        async with async_session_factory() as cleanup_session:
            await cleanup_session.execute(delete(UserOAuthAccount).where(UserOAuthAccount.user_id == user.id))
            await cleanup_session.execute(delete(User).where(User.id == user.id))
            await cleanup_session.commit()


async def test_linked_entra_id_authenticates_same_local_user_after_upn_change(
    db_session, async_session_factory, monkeypatch
):
    """The permanent Entra object ID wins after a linked user's UPN changes."""
    photo = HttpResponse(404, request=HttpRequest("GET", MICROSOFT_GRAPH_PHOTO_URL))
    async with _entra_callback(db_session, async_session_factory, monkeypatch, photo) as (
        response,
        provider_client,
        user,
        issue_session,
        provision,
    ):
        assert response.status_code == 200
        issue_session.assert_awaited_once_with(user.id)
        provision.assert_not_awaited()
        assert provider_client.requests == [
            ("https://graph.example.test/me", {"Authorization": "Bearer provider-access-token"}),
            (MICROSOFT_GRAPH_PHOTO_URL, {"Authorization": "Bearer provider-access-token"}),
        ]

        async with async_session_factory() as verification_session:
            linked_user_id = await verification_session.scalar(
                select(UserOAuthAccount.user_id).where(
                    UserOAuthAccount.provider_id == "microsoft",
                    UserOAuthAccount.provider_user_id == provider_client.userinfo.json()["id"],
                )
            )
            stored = await verification_session.get(User, user.id)
        assert linked_user_id == user.id
        assert stored is not None
        assert stored.avatar_data is None


async def test_entra_sign_in_stores_the_graph_profile_photo(
    db_session, async_session_factory, monkeypatch
):
    """A Graph profile photo from Entra sign-in is saved on the user."""
    png = _png()
    photo = HttpResponse(200, content=png, request=HttpRequest("GET", MICROSOFT_GRAPH_PHOTO_URL))
    async with _entra_callback(db_session, async_session_factory, monkeypatch, photo) as (
        response,
        _provider_client,
        user,
        _issue_session,
        _provision,
    ):
        assert response.status_code == 200
        async with async_session_factory() as verification_session:
            stored = await verification_session.get(User, user.id)
        assert stored is not None
        assert stored.avatar_content_type == "image/png"
        assert stored.avatar_data == png
