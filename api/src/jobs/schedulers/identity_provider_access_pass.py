"""Apply identity provider grants in one transaction every two minutes."""

import logging

from src.core.database import get_session_factory
from src.core.database_retry import is_transaction_conflict
from src.services.identity_provisioning.access_pass import run_access_pass

logger = logging.getLogger(__name__)


async def run_identity_provider_access_pass() -> dict:
    """Apply the activations, role grants and renames provisioning deferred."""
    session_factory = get_session_factory()
    try:
        async with session_factory() as db:
            result = await run_access_pass(db)
            await db.commit()
    except Exception as exc:
        if not is_transaction_conflict(exc):
            raise
        # A deadlock outside a link's savepoint (taking the run's locks, or
        # at commit) rolls back the whole run; every link still waits.
        logger.warning(
            "Identity provider access pass was rolled back to break a deadlock; "
            "the next run applies its links"
        )
        return {"applied": 0, "failed": 0, "conflicts": 1}
    for provisioner in result.provisioners:
        await provisioner.after_commit()
    summary = result.summary()
    if result.applied or result.failed or result.conflicts:
        logger.info("Identity provider access pass: %s", summary)
    return summary
