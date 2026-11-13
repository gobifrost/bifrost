"""Cleanup old worker metrics rows (7-day retention)."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from src.core.database import get_session_factory
from src.models.orm.worker_metric import WorkerMetric

logger = logging.getLogger(__name__)


async def cleanup_old_worker_metrics() -> dict:
    """
    Delete worker_metrics rows older than 7 days.

    Returns:
        Summary with rows_deleted count.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    try:
        session_factory = get_session_factory()
        async with session_factory() as db:
            stmt = delete(WorkerMetric).where(WorkerMetric.timestamp < cutoff)
            result = await db.execute(stmt)
            await db.commit()
            deleted = result.rowcount

        logger.info(f"Worker metrics cleanup: deleted {deleted} rows older than {cutoff.isoformat()}")
        return {"rows_deleted": deleted}
    except Exception as e:
        logger.exception(f"Worker metrics cleanup failed: {e}", exc_info=True)
        return {"rows_deleted": 0, "error": str(e)}
