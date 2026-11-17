"""Apply identity provider grants in one transaction every two minutes."""

import logging

from src.core.database import get_session_factory
from src.services.identity_provisioning.access_pass import run_access_pass

logger = logging.getLogger(__name__)


async def run_identity_provider_access_pass() -> dict:
    """Apply the activations, role grants and renames provisioning deferred."""
    session_factory = get_session_factory()
    async with session_factory() as db:
        result = await run_access_pass(db)
        await db.commit()
    for provisioner in result.provisioners:
        await provisioner.after_commit()
    summary = result.summary()
    if result.applied or result.failed:
        logger.info("Identity provider access pass: %s", summary)
    return summary
