"""Hourly check for identity provider tokens close to or past expiry."""

import logging
from datetime import datetime, timezone

from src.core.database import get_session_factory
from src.services.identity_provisioning.expiry import (
    announce_expiry_events,
    collect_expiry_notices,
    raise_expiry_notifications,
)

logger = logging.getLogger(__name__)


async def check_identity_provider_token_expiry() -> dict:
    """Warn about tokens that expire within 14 days or have expired."""
    session_factory = get_session_factory()
    async with session_factory() as db:
        notices = await collect_expiry_notices(db, datetime.now(timezone.utc))
        await raise_expiry_notifications(notices)
        await db.commit()
    await announce_expiry_events(notices)
    summary = {
        "expiring": sum(1 for n in notices if n.stage == "expiring"),
        "expired": sum(1 for n in notices if n.stage == "expired"),
        "announced": sum(1 for n in notices if n.announce),
    }
    if notices:
        logger.info("Identity provider token expiry check: %s", summary)
    return summary
