"""``user.created`` and ``user.signed_in`` are recorded on the real sign-in paths."""

import dataclasses
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Request as HttpRequest, Response as HttpResponse
from sqlalchemy import delete, select

from src.core.cache import get_shared_redis
from src.core.cache.keys import oauth_state_key
from src.core.database import get_db
from src.models import Organization, User, UserOAuthAccount
from src.models.orm.events import Event
from src.routers import oauth_sso
from src.services.oauth_sso import MICROSOFT_GRAPH_PHOTO_URL, OAuthService, OAuthTokens
from tests.e2e.fixtures.setup import _login_user

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.usefixtures("user_topic_sources")]

USER_TOPICS = ("user.created", "user.signed_in")


@pytest.fixture(scope="module")
def user_topic_sources(e2e_client, platform_admin):
    """One global topic source per user topic, so emits are recorded."""
    sources = []
    for topic in USER_TOPICS:
        response = e2e_client.post(
            "/api/events/sources",
            headers=platform_admin.headers,
            json={"name": f"E2E {topic}", "source_type": "topic", "event_type": topic},
        )
        assert response.status_code == 201, response.text
        sources.append(response.json())
    yield sources
    for source in sources:
        e2e_client.delete(f"/api/events/sources/{source['id']}", headers=platform_admin.headers)


async def _user_events(session_factory, topic: str, user_id: str) -> list[dict]:
    async with session_factory() as session:
        rows = await session.scalars(
            select(Event.data).where(
                Event.event_type == topic,
                Event.data["user"]["id"].astext == user_id,
            )
        )
        return list(rows)


async def test_admin_created_user_emits_user_created(
    e2e_client, platform_admin, org1, async_session_factory
):
    response = e2e_client.post(
        "/api/users",
        headers=platform_admin.headers,
        json={
            "email": f"created-event-{uuid4().hex[:8]}@gobifrost.dev",
            "name": "Created Event",
            "organization_id": org1["id"],
        },
    )
    assert response.status_code == 201, response.text
    user_id = response.json()["id"]
    try:
        events = await _user_events(async_session_factory, "user.created", user_id)
        assert len(events) == 1
        assert events[0]["creation"] == {"method": "admin", "provider": None}
        assert events[0]["user"]["organization_id"] == org1["id"]
        assert events[0]["actor"]["id"] == str(platform_admin.user_id)
    finally:
        e2e_client.patch(
            f"/api/users/{user_id}", headers=platform_admin.headers, json={"is_active": False}
        )
        e2e_client.delete(f"/api/users/{user_id}", headers=platform_admin.headers)


async def test_password_sign_in_emits_user_signed_in(
    org1_user, async_session_factory
):
    before = await _user_events(async_session_factory, "user.signed_in", str(org1_user.user_id))

    _login_user(dataclasses.replace(org1_user))

    events = await _user_events(async_session_factory, "user.signed_in", str(org1_user.user_id))
    assert len(events) == len(before) + 1
    new = [event for event in events if event not in before]
    assert new[0]["sign_in"] == {"method": "password", "provider": None}
    assert new[0]["identity"] is None
    assert new[0]["user"]["email"] == org1_user.email


class _GraphClient:
    def __init__(self, userinfo: dict):
        self.userinfo = userinfo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url: str, *, headers: dict[str, str]):
        return HttpResponse(200, json=self.userinfo, request=HttpRequest("GET", url))

    def stream(self, method: str, url: str, *, headers: dict[str, str]):
        response = HttpResponse(404, request=HttpRequest(method, MICROSOFT_GRAPH_PHOTO_URL))

        class _Stream:
            async def __aenter__(self):
                return response

            async def __aexit__(self, *_args):
                return None

        return _Stream()


async def test_first_sso_sign_in_emits_created_and_signed_in(
    db_session, async_session_factory, monkeypatch
):
    """A provisioning Entra sign-in reports both topics with the verified groups."""
    domain = f"sso-events-{uuid4().hex[:8]}.example"
    email = f"kim@{domain}"
    oid = str(uuid4())
    org = Organization(id=uuid4(), name=f"SSO Events {domain}", domain=domain, created_by="e2e")
    async with async_session_factory() as session:
        session.add(org)
        await session.commit()

    state = f"sso-events-{uuid4()}"
    redis = await get_shared_redis()
    await redis.setex(
        oauth_state_key(state),
        600,
        json.dumps(
            {
                "version": 2,
                "code_verifier": "verifier",
                "redirect_uri": "https://app.test/callback",
                "nonce": "test-nonce",
            }
        ),
    )
    claims = {
        "tid": "work-tenant",
        "oid": oid,
        "groups": ["group-a", "group-b"],
        "nonce": "test-nonce",
        "at_hash": "binding",
    }
    monkeypatch.setattr(
        OAuthService,
        "exchange_code_for_tokens",
        AsyncMock(
            return_value=OAuthTokens(
                access_token="provider-access-token",
                id_token="raw.id.token",
                id_token_claims=claims,
            )
        ),
    )
    monkeypatch.setattr(
        OAuthService,
        "get_provider_config",
        AsyncMock(return_value={"userinfo_url": "https://graph.example.test/me"}),
    )
    monkeypatch.setattr(
        "src.services.oauth_sso.httpx.AsyncClient",
        lambda *_args, **_kwargs: _GraphClient(
            {"id": oid, "displayName": "Kim Braun", "userPrincipalName": email}
        ),
    )
    monkeypatch.setattr(
        oauth_sso,
        "issue_human_session",
        AsyncMock(return_value=SimpleNamespace(access_token="access", refresh_token="refresh")),
    )
    monkeypatch.setattr(oauth_sso, "set_auth_cookies", lambda *_args: None)

    async def db_dependency():
        yield db_session

    app = FastAPI()
    app.include_router(oauth_sso.router)
    app.dependency_overrides[get_db] = db_dependency

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://bifrost.test") as client:
            response = await client.post(
                "/auth/oauth/callback",
                json={"provider": "microsoft", "code": "code", "state": state},
            )
        assert response.status_code == 200, response.text

        async with async_session_factory() as session:
            user_id = str(await session.scalar(select(User.id).where(User.email == email)))

        created = await _user_events(async_session_factory, "user.created", user_id)
        signed_in = await _user_events(async_session_factory, "user.signed_in", user_id)
        assert len(created) == 1
        assert created[0]["creation"] == {"method": "sso_provisioning", "provider": "microsoft"}
        assert created[0]["user"]["organization_id"] == str(org.id)
        assert len(signed_in) == 1
        assert signed_in[0]["sign_in"] == {"method": "sso", "provider": "microsoft"}
        assert signed_in[0]["identity"] == {
            "claims": {"tid": "work-tenant", "oid": oid, "groups": ["group-a", "group-b"]},
            "groups": ["group-a", "group-b"],
            "groups_overage": False,
        }
        assert "raw.id.token" not in json.dumps(signed_in[0])
        assert "provider-access-token" not in json.dumps(signed_in[0])
    finally:
        await redis.delete(oauth_state_key(state))
        await db_session.rollback()
        async with async_session_factory() as cleanup:
            user_ids = select(User.id).where(User.email == email).scalar_subquery()
            await cleanup.execute(delete(UserOAuthAccount).where(UserOAuthAccount.user_id == user_ids))
            await cleanup.execute(delete(User).where(User.email == email))
            await cleanup.execute(delete(Organization).where(Organization.id == org.id))
            await cleanup.commit()
