"""Token-expiry warnings: which tokens need attention, and each stage is
announced once."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
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


def _notice(*, announce: bool, identity_provider_id=None) -> ExpiryNotice:
    return ExpiryNotice(
        identity_provider_id=identity_provider_id or uuid4(),
        identity_provider_name="Contoso Entra",
        vendor="entra",
        organization_id=uuid4(),
        organization_name="Contoso",
        token_hint="abcd",
        expires_at=NOW + timedelta(days=5),
        stage="expiring",
        announce=announce,
    )


class _Notifications:
    """The notification service's admin notifications, in memory."""

    def __init__(self) -> None:
        self.raised: dict[str, dict] = {}
        self.dismissed: list[str] = []

    async def find_admin_notification_by_metadata(self, metadata, category=None):
        for notification_id, held in self.raised.items():
            if all(held.get(key) == value for key, value in metadata.items()):
                return SimpleNamespace(id=notification_id)
        return None

    async def create_notification(self, *, user_id, request, for_admins, initial_status):
        self.raised[f"n{len(self.raised)}"] = request.metadata

    async def dismiss_notification(self, notification_id, user_id):
        self.dismissed.append(notification_id)
        del self.raised[notification_id]


@pytest.mark.asyncio
async def test_delivery_announces_new_stages_and_keeps_one_notification():
    service = _Notifications()
    emit = AsyncMock()
    connection_id = uuid4()
    with (
        patch("src.services.notification_service.get_notification_service", return_value=service),
        patch("src.services.events.builtins.emit_identity_provider_token_expiry", emit),
    ):
        await deliver_expiry_notices(
            [
                _notice(announce=True, identity_provider_id=connection_id),
                _notice(announce=False, identity_provider_id=connection_id),
            ]
        )

    assert emit.await_count == 1
    assert emit.await_args.kwargs["stage"] == "expiring"
    # The second notice's notification already exists, so only one is raised.
    assert len(service.raised) == 1
    (metadata,) = service.raised.values()
    assert metadata["action_url"].startswith("/users/identity-providers/")


@pytest.mark.asyncio
async def test_same_named_connections_keep_their_own_warnings():
    from src.services.identity_provisioning.expiry import clear_expiry_notifications

    service = _Notifications()
    rotated, other = uuid4(), uuid4()
    with (
        patch("src.services.notification_service.get_notification_service", return_value=service),
        patch("src.services.events.builtins.emit_identity_provider_token_expiry", AsyncMock()),
    ):
        # Two organizations name their connection the same.
        await deliver_expiry_notices(
            [
                _notice(announce=True, identity_provider_id=rotated),
                _notice(announce=True, identity_provider_id=other),
            ]
        )
        assert len(service.raised) == 2

        await clear_expiry_notifications(rotated)

    (remaining,) = service.raised.values()
    assert remaining["identity_provider_id"] == str(other)
