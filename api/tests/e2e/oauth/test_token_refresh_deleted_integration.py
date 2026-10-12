"""E2E: the OAuth refresh scheduler skips tokens of soft-deleted integrations.

Integrations deleted before deletion started removing OAuth credentials
still have provider and token rows. Nobody can see or disconnect them, so
the scheduler must not refresh them (each failure emits refresh_failed and
reauth_required events every run).
"""

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from src.jobs.schedulers import oauth_token_refresh as sched
from src.models.orm import Integration, OAuthProvider, OAuthToken

pytestmark = pytest.mark.e2e


async def _add_integration_with_token(
    db_session, *, is_deleted: bool
) -> tuple[UUID, UUID, UUID]:
    """Returns (integration_id, provider_id, token_id)."""
    integration = Integration(name=f"e2e_refresh_{uuid4().hex[:8]}", is_deleted=is_deleted)
    db_session.add(integration)
    await db_session.flush()
    provider = OAuthProvider(
        provider_name=f"e2e_refresh_{uuid4().hex[:6]}",
        oauth_flow_type="authorization_code",
        client_id="client",
        encrypted_client_secret=b"secret",
        token_url="https://login.example.com/token",
        integration_id=integration.id,
    )
    db_session.add(provider)
    await db_session.flush()
    # No expires_at, so the token is always due for refresh.
    token = OAuthToken(
        provider_id=provider.id,
        encrypted_access_token=b"access",
        encrypted_refresh_token=b"refresh",
    )
    db_session.add(token)
    await db_session.flush()
    return integration.id, provider.id, token.id


async def test_refresh_skips_tokens_of_deleted_integrations(db_session):
    live = await _add_integration_with_token(db_session, is_deleted=False)
    deleted = await _add_integration_with_token(db_session, is_deleted=True)
    await db_session.commit()

    attempted = []

    async def _record_attempt(td):
        attempted.append(td["token_id"])
        # Raising keeps the job from persisting refresh results onto any
        # token rows, including ones other tests own.
        raise RuntimeError("refresh stubbed out by test")

    try:
        with patch.object(
            sched,
            "refresh_oauth_token_http",
            new_callable=AsyncMock,
            side_effect=_record_attempt,
        ):
            await sched.run_refresh_job(trigger_type="manual")
    finally:
        await db_session.execute(delete(OAuthToken).where(OAuthToken.id.in_([live[2], deleted[2]])))
        await db_session.execute(
            delete(OAuthProvider).where(OAuthProvider.id.in_([live[1], deleted[1]]))
        )
        await db_session.execute(
            delete(Integration).where(Integration.id.in_([live[0], deleted[0]]))
        )
        await db_session.commit()

    assert live[2] in attempted
    assert deleted[2] not in attempted
