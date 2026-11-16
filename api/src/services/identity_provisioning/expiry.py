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
from typing import Literal
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
    newly reached stages on the tokens; the caller commits, then delivers."""
    rows = (
        await session.execute(
            select(IdentityProvider, IdentityProviderToken, Organization.name)
            .join(
                IdentityProviderToken,
                IdentityProviderToken.identity_provider_id == IdentityProvider.id,
            )
            .join(Organization, Organization.id == IdentityProvider.organization_id)
            .where(
                IdentityProvider.is_enabled.is_(True),
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


def _titles(identity_provider_name: str) -> dict[Stage, str]:
    return {
        "expiring": f"SCIM Token Expiring: {identity_provider_name}",
        "expired": f"SCIM Token Expired: {identity_provider_name}",
    }


def notification_title(notice: ExpiryNotice) -> str:
    return _titles(notice.identity_provider_name)[notice.stage]


async def clear_expiry_notifications(identity_provider_name: str) -> None:
    """Dismiss a connection's expiry warnings once a new token is issued."""
    from src.models.contracts.notifications import NotificationCategory
    from src.services.notification_service import get_notification_service

    service = get_notification_service()
    for title in _titles(identity_provider_name).values():
        existing = await service.find_admin_notification_by_title(
            title=title, category=NotificationCategory.SYSTEM
        )
        if existing:
            await service.dismiss_notification(existing.id, user_id="system")


async def deliver_expiry_notices(notices: list[ExpiryNotice]) -> None:
    """Send events for new stages and keep admin notifications up."""
    from src.models.contracts.notifications import (
        NotificationCategory,
        NotificationCreate,
        NotificationStatus,
    )
    from src.services.events.builtins import emit_identity_provider_token_expiry
    from src.services.notification_service import get_notification_service

    service = get_notification_service()
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
        title = notification_title(notice)
        if await service.find_admin_notification_by_title(
            title=title, category=NotificationCategory.SYSTEM
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
                    "identity_provider_id": str(notice.identity_provider_id),
                },
            ),
            for_admins=True,
            initial_status=NotificationStatus.AWAITING_ACTION,
        )
