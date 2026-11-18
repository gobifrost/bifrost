"""Token-expiry warnings for identity provider connections.

An expired token silently stops provisioning: the identity provider's
requests fail and nobody in Bifrost sees them. An hourly scheduled check
looks at each enabled connection's current token and, while it is within
``EXPIRY_WARNING`` of expiry or past it:

- keeps a platform-admin notification up (notifications are Redis-backed
  and live an hour, so each run re-raises one that lapsed), and
- emits ``identity_provider.token_expiry`` once per stage, recorded on the
  token, so a workflow can tell the organization's own admins.

A rotated token starts with no stage; superseded tokens are ignored.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, get_args
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.identity_providers import IdentityProvider, IdentityProviderToken
from src.models.orm.organizations import Organization

logger = logging.getLogger(__name__)

EXPIRY_WARNING = timedelta(days=14)

Stage = Literal["expiring", "expired"]


def token_stage(expires_at: datetime, now: datetime) -> Stage | None:
    if expires_at <= now:
        return "expired"
    if expires_at - now <= EXPIRY_WARNING:
        return "expiring"
    return None


@dataclass(frozen=True)
class ExpiryNotice:
    identity_provider_id: UUID
    identity_provider_name: str
    vendor: str
    organization_id: UUID
    organization_name: str
    token_hint: str
    expires_at: datetime
    stage: Stage
    # True the first time this token reaches this stage.
    announce: bool


async def collect_expiry_notices(session: AsyncSession, now: datetime) -> list[ExpiryNotice]:
    """Each enabled connection whose current token needs attention. Marks
    newly reached stages on the tokens.

    The caller raises the notifications, then commits, then announces the
    events. The connection rows stay key-share locked until the commit, so
    a rotation (which locks them for update) either lands before this reads
    the tokens or waits until the warnings are up, then dismisses them.
    """
    locked = list(
        await session.scalars(
            select(IdentityProvider.id)
            .where(IdentityProvider.is_enabled.is_(True))
            .order_by(IdentityProvider.id)
            # FOR KEY SHARE: SCIM requests' last_request_at updates don't
            # wait on it; only rotation's FOR UPDATE does.
            .with_for_update(read=True, key_share=True)
        )
    )
    rows = (
        await session.execute(
            select(IdentityProvider, IdentityProviderToken, Organization.name)
            .join(
                IdentityProviderToken,
                IdentityProviderToken.identity_provider_id == IdentityProvider.id,
            )
            .join(Organization, Organization.id == IdentityProvider.organization_id)
            .where(
                # Only the connections locked above, enabled since or not.
                IdentityProvider.id.in_(locked),
                IdentityProvider.is_enabled.is_(True),
                Organization.is_active.is_(True),
                IdentityProviderToken.revoked_at.is_(None),
            )
            .order_by(IdentityProvider.id, IdentityProviderToken.created_at.desc())
            .distinct(IdentityProvider.id)
        )
    ).all()
    notices: list[ExpiryNotice] = []
    for connection, token, organization_name in rows:
        stage = token_stage(token.expires_at, now)
        if stage is None:
            continue
        announce = token.expiry_warning_stage != stage
        if announce:
            token.expiry_warning_stage = stage
        notices.append(
            ExpiryNotice(
                identity_provider_id=connection.id,
                identity_provider_name=connection.name,
                vendor=connection.vendor,
                organization_id=connection.organization_id,
                organization_name=organization_name,
                token_hint=token.token_hint,
                expires_at=token.expires_at,
                stage=stage,
                announce=announce,
            )
        )
    await session.flush()
    return notices


def notification_title(notice: ExpiryNotice) -> str:
    verb = "Expired" if notice.stage == "expired" else "Expiring"
    return f"SCIM Token {verb}: {notice.identity_provider_name}"


def _notification_key(identity_provider_id: UUID, stage: Stage) -> dict[str, str]:
    # Matched by connection id: connection names repeat across organizations.
    return {"identity_provider_id": str(identity_provider_id), "token_stage": stage}


async def clear_expiry_notifications(identity_provider_id: UUID) -> None:
    """Dismiss a connection's expiry warnings once a new token is issued."""
    from src.models.contracts.notifications import NotificationCategory
    from src.services.notification_service import get_notification_service

    service = get_notification_service()
    for stage in get_args(Stage):
        existing = await service.find_admin_notification_by_metadata(
            _notification_key(identity_provider_id, stage), category=NotificationCategory.SYSTEM
        )
        if existing:
            await service.dismiss_notification(existing.id, user_id="system")


async def announce_expiry_events(notices: list[ExpiryNotice]) -> None:
    """Send an event for each newly reached stage, after the commit that
    recorded it."""
    from src.services.events.builtins import emit_identity_provider_token_expiry

    for notice in notices:
        if notice.announce:
            await emit_identity_provider_token_expiry(
                organization_id=notice.organization_id,
                organization_name=notice.organization_name,
                identity_provider={
                    "id": str(notice.identity_provider_id),
                    "name": notice.identity_provider_name,
                    "vendor": notice.vendor,
                },
                token_hint=notice.token_hint,
                expires_at=notice.expires_at,
                stage=notice.stage,
            )


async def raise_expiry_notifications(notices: list[ExpiryNotice]) -> None:
    """Keep one admin notification up per connection and stage (before the
    commit; see :func:`collect_expiry_notices`)."""
    from src.models.contracts.notifications import (
        NotificationCategory,
        NotificationCreate,
        NotificationStatus,
    )
    from src.services.notification_service import get_notification_service

    service = get_notification_service()
    for notice in notices:
        title = notification_title(notice)
        key = _notification_key(notice.identity_provider_id, notice.stage)
        if await service.find_admin_notification_by_metadata(
            key, category=NotificationCategory.SYSTEM
        ):
            continue
        when = notice.expires_at.strftime("%b %d, %Y")
        description = (
            f"{notice.organization_name}: the token "
            f"{'expired' if notice.stage == 'expired' else 'expires'} {when}. "
            "Regenerate it and update the identity provider."
        )
        await service.create_notification(
            user_id="system",
            request=NotificationCreate(
                category=NotificationCategory.SYSTEM,
                title=title,
                description=description,
                metadata={
                    "action": "open_identity_provider",
                    "action_label": "Regenerate Token",
                    "action_url": f"/users/identity-providers/{notice.identity_provider_id}",
                    **key,
                },
            ),
            for_admins=True,
            initial_status=NotificationStatus.AWAITING_ACTION,
        )
