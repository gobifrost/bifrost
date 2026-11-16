"""Token-expiry warnings: which tokens need attention, and each stage is
announced once."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio

from src.models.orm.identity_providers import IdentityProvider, IdentityProviderToken
from src.models.orm.organizations import Organization
from src.services.identity_provisioning.expiry import (
    ExpiryNotice,
    collect_expiry_notices,
    deliver_expiry_notices,
    token_stage,
)

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def test_stages():
    assert token_stage(NOW + timedelta(days=30), NOW) is None
    assert token_stage(NOW + timedelta(days=14), NOW) == "expiring"
    assert token_stage(NOW, NOW) == "expired"


@pytest_asyncio.fixture
async def connection(db_session):
    organization = Organization(name=f"Contoso {uuid4().hex[:8]}", created_by="test")
    db_session.add(organization)
    await db_session.flush()
    connection = IdentityProvider(
        organization_id=organization.id,
        name="Contoso Entra",
        source_type="scim",
        vendor="entra",
        created_by="test",
    )
    db_session.add(connection)
    await db_session.flush()
    return connection


async def _token(session, connection, expires_in: timedelta, *, created_at: datetime = NOW):
    token = IdentityProviderToken(
        identity_provider_id=connection.id,
        token_hash=uuid4().hex,
        token_hint="abcd",
        expires_at=NOW + expires_in,
        created_by="test",
        created_at=created_at,
    )
    session.add(token)
    await session.flush()
    return token


async def _notices_for(session, connection):
    return [
        n
        for n in await collect_expiry_notices(session, NOW)
        if n.identity_provider_id == connection.id
    ]


@pytest.mark.asyncio
async def test_each_stage_is_announced_once(db_session, connection):
    token = await _token(db_session, connection, timedelta(days=5))

    first = await _notices_for(db_session, connection)
    second = await _notices_for(db_session, connection)

    assert [(n.stage, n.announce) for n in first] == [("expiring", True)]
    assert [(n.stage, n.announce) for n in second] == [("expiring", False)]
    assert token.expiry_warning_stage == "expiring"

    token.expires_at = NOW - timedelta(minutes=1)
    await db_session.flush()
    assert [(n.stage, n.announce) for n in await _notices_for(db_session, connection)] == [
        ("expired", True)
    ]


@pytest.mark.asyncio
async def test_only_the_current_token_counts(db_session, connection):
    # A rotation leaves the old token alive for an hour; that's not a warning.
    await _token(db_session, connection, timedelta(minutes=30), created_at=NOW - timedelta(days=1))
    await _token(db_session, connection, timedelta(days=365))

    assert await _notices_for(db_session, connection) == []


@pytest.mark.asyncio
async def test_disabled_connections_are_skipped(db_session, connection):
    await _token(db_session, connection, timedelta(days=-1))
    connection.is_enabled = False
    await db_session.flush()

    assert await _notices_for(db_session, connection) == []


def _notice(*, announce: bool) -> ExpiryNotice:
    return ExpiryNotice(
        identity_provider_id=uuid4(),
        identity_provider_name="Contoso Entra",
        vendor="entra",
        organization_id=uuid4(),
        organization_name="Contoso",
        token_hint="abcd",
        expires_at=NOW + timedelta(days=5),
        stage="expiring",
        announce=announce,
    )


@pytest.mark.asyncio
async def test_delivery_announces_new_stages_and_keeps_one_notification():
    service = AsyncMock()
    service.find_admin_notification_by_title.side_effect = [None, object()]
    emit = AsyncMock()
    with (
        patch("src.services.notification_service.get_notification_service", return_value=service),
        patch("src.services.events.builtins.emit_identity_provider_token_expiry", emit),
    ):
        await deliver_expiry_notices([_notice(announce=True), _notice(announce=False)])

    assert emit.await_count == 1
    assert emit.await_args.kwargs["stage"] == "expiring"
    # The second notice's notification already exists, so only one is raised.
    assert service.create_notification.await_count == 1
    metadata = service.create_notification.await_args.kwargs["request"].metadata
    assert metadata["action_url"].startswith("/users/identity-providers/")


@pytest.mark.asyncio
async def test_rotation_dismisses_outstanding_warnings():
    from src.services.identity_provisioning.expiry import clear_expiry_notifications

    expired = AsyncMock()
    expired.id = "n-expired"
    service = AsyncMock()
    service.find_admin_notification_by_title.side_effect = (
        lambda title, category: expired if title.startswith("SCIM Token Expired") else None
    )
    with patch(
        "src.services.notification_service.get_notification_service", return_value=service
    ):
        await clear_expiry_notifications("Contoso Entra")

    service.dismiss_notification.assert_awaited_once_with("n-expired", user_id="system")
