"""Unit tests for oauth_token_refresh scheduler — per-token status writes.

Verifies:
- Per-token status fields (status, status_message, last_refresh_at) are always written.
- OAuthProvider status is only updated for integration-level (org_id IS NULL) tokens.
- Per-org tokens do NOT poison provider status on failure.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import OAuthProvider, OAuthToken
from src.models.orm.organizations import Organization
from src.jobs.schedulers import oauth_token_refresh as mod

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixture: redirect scheduler DB sessions to the test's session factory
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_scheduler_db(monkeypatch, async_session_factory):
    """
    Replace get_db_context in the scheduler module with one that uses
    the test's async_session_factory (NullPool, no loop affinity issues).

    The scheduler calls get_db_context() twice:
    - Phase 1: load tokens to refresh
    - Phase 3: persist results

    Both need to see committed test data, so we use the same factory
    that the db_session fixture uses.
    """

    @asynccontextmanager
    async def _test_db_context() -> AsyncGenerator[AsyncSession, None]:
        async with async_session_factory() as session:
            yield session

    monkeypatch.setattr(mod, "get_db_context", _test_db_context)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_org(db: AsyncSession) -> Organization:
    org = Organization(
        id=uuid4(),
        name=f"test-org-{uuid4().hex[:8]}",
        created_by="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(org)
    await db.flush()
    return org


async def _make_provider(db: AsyncSession, *, oauth_flow_type: str = "authorization_code") -> OAuthProvider:
    provider = OAuthProvider(
        id=uuid4(),
        provider_name=f"test-provider-{uuid4().hex[:8]}",
        client_id="test-client-id",
        encrypted_client_secret=b"secret",
        oauth_flow_type=oauth_flow_type,
        token_url="https://example.com/token",
        status="completed",
        status_message=None,
    )
    db.add(provider)
    await db.flush()
    return provider


async def _make_token(
    db: AsyncSession,
    provider: OAuthProvider,
    *,
    organization_id: object = None,
    expires_past: bool = True,
) -> OAuthToken:
    expires_at = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
        if expires_past
        else datetime.now(timezone.utc) + timedelta(hours=1)
    )
    token = OAuthToken(
        id=uuid4(),
        provider_id=provider.id,
        organization_id=organization_id,
        encrypted_access_token=b"access",
        encrypted_refresh_token=b"refresh",  # required so Phase 1 WHERE picks it up
        expires_at=expires_at,
        status="not_connected",
        status_message=None,
        last_refresh_at=None,
    )
    db.add(token)
    await db.flush()
    return token


def _make_success_outcome(token: OAuthToken, provider: OAuthProvider) -> dict:
    return {
        "success": True,
        "token_id": token.id,
        "provider_id": provider.id,
        "encrypted_access_token": b"new-access",
        "encrypted_refresh_token": None,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "scopes": [],
    }


def _make_failure_outcome(token: OAuthToken, provider: OAuthProvider, error: str = "Token endpoint 400") -> dict:
    return {
        "success": False,
        "token_id": token.id,
        "provider_id": provider.id,
        "error": error,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_per_token_success_writes_token_status(db_session: AsyncSession, monkeypatch):
    """On success, token.status == 'completed' and token.last_refresh_at is set."""
    org = await _make_org(db_session)
    provider = await _make_provider(db_session)
    token = await _make_token(db_session, provider, organization_id=org.id)
    await db_session.commit()

    success_outcome = _make_success_outcome(token, provider)

    async def fake_http(td):
        return success_outcome

    monkeypatch.setattr(mod, "refresh_oauth_token_http", fake_http)

    await mod.run_refresh_job(trigger_type="manual")

    await db_session.refresh(token)
    assert token.status == "completed"
    assert token.status_message is None
    assert token.last_refresh_at is not None


async def test_per_token_failure_writes_token_status_message(db_session: AsyncSession, monkeypatch):
    """On failure, token.status == 'failed' and token.status_message contains the error."""
    org = await _make_org(db_session)
    provider = await _make_provider(db_session)
    token = await _make_token(db_session, provider, organization_id=org.id)
    await db_session.commit()

    error_msg = "Token endpoint returned 400"
    failure_outcome = _make_failure_outcome(token, provider, error=error_msg)

    async def fake_http(td):
        return failure_outcome

    monkeypatch.setattr(mod, "refresh_oauth_token_http", fake_http)

    await mod.run_refresh_job(trigger_type="manual")

    await db_session.refresh(token)
    assert token.status == "failed"
    assert token.status_message is not None
    assert error_msg in token.status_message
    assert token.last_refresh_at is not None


async def test_org_scoped_token_failure_does_not_touch_provider_status(db_session: AsyncSession, monkeypatch):
    """When a per-org token (organization_id IS NOT NULL) fails, provider status stays unchanged."""
    org = await _make_org(db_session)
    provider = await _make_provider(db_session)
    # Mark provider as completed so we can verify it stays that way
    provider.status = "completed"
    provider.status_message = None

    token = await _make_token(db_session, provider, organization_id=org.id)
    await db_session.commit()

    failure_outcome = _make_failure_outcome(token, provider)

    async def fake_http(td):
        return failure_outcome

    monkeypatch.setattr(mod, "refresh_oauth_token_http", fake_http)

    await mod.run_refresh_job(trigger_type="manual")

    await db_session.refresh(provider)
    # Provider status must remain "completed" — per-org token failure must not poison it
    assert provider.status == "completed"
    assert provider.status_message is None

    # But the token itself should be marked failed
    await db_session.refresh(token)
    assert token.status == "failed"


async def test_integration_level_token_failure_updates_provider_status(db_session: AsyncSession, monkeypatch):
    """When a token with organization_id IS NULL fails, provider status DOES get updated."""
    provider = await _make_provider(db_session)
    provider.status = "completed"

    # organization_id=None → integration-level fallback token
    token = await _make_token(db_session, provider, organization_id=None)
    await db_session.commit()

    error_msg = "Integration-level refresh failed"
    failure_outcome = _make_failure_outcome(token, provider, error=error_msg)

    async def fake_http(td):
        return failure_outcome

    monkeypatch.setattr(mod, "refresh_oauth_token_http", fake_http)

    await mod.run_refresh_job(trigger_type="manual")

    await db_session.refresh(provider)
    assert provider.status == "failed"
    assert provider.status_message is not None
    assert error_msg in provider.status_message

    # Token itself should also be marked failed
    await db_session.refresh(token)
    assert token.status == "failed"
