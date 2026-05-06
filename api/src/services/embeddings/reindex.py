"""
Knowledge-store reindex service.

Re-embeds every row in `knowledge_store` against the currently-configured
embedder. Runs on the scheduler container (see api/src/scheduler/main.py),
triggered via the `bifrost:scheduler:embedding-reindex` Redis channel.

Progress + cancellation flow through the existing NotificationService /
WebSocket pipeline; the client subscribes to `notification:{user_id}` and
renders progress without polling.

Cancellation is best-effort: the scheduler checks
`bifrost:notification:{notification_id}:cancelled` between batches and bails
cleanly, leaving partial state. There's no rollback — partial state is the
deliberate trade-off the user agreed to when they confirmed.
"""

from __future__ import annotations

import logging
from typing import cast

from sqlalchemy import select, update

from src.core.database import get_db_context
from src.core.redis_client import get_redis_client
from src.models.contracts.notifications import (
    NotificationStatus,
    NotificationUpdate,
)
from src.models.orm.knowledge import KnowledgeStore
from src.services.embeddings.factory import get_embedding_client
from src.services.notification_service import get_notification_service

logger = logging.getLogger(__name__)


# Embedding-API batch size — smaller than OpenAI's 2048 max but plenty large
# for round-trip efficiency. Progress notifications fire per-row, not per-batch,
# so this doesn't affect UI smoothness.
EMBED_BATCH_SIZE = 256


def _cancel_key(notification_id: str) -> str:
    return f"bifrost:notification:{notification_id}:cancelled"


async def is_cancelled(notification_id: str) -> bool:
    """Check the Redis cancellation flag set by DELETE /api/notifications/{id}."""
    redis_client = get_redis_client()
    if redis_client is None:
        return False
    try:
        value = await redis_client.get(_cancel_key(notification_id))
    except Exception as e:
        logger.warning(f"Failed to read cancel flag for {notification_id}: {e}")
        return False
    return value is not None


async def mark_cancelled(notification_id: str) -> None:
    """Set the Redis cancellation flag. Called from the notifications router."""
    redis_client = get_redis_client()
    if redis_client is None:
        return
    # 1-hour TTL matches ACTIVE_NOTIFICATION_TTL — flag dies with the notification.
    await redis_client.setex(_cancel_key(notification_id), 3600, "1")


async def clear_cancel_flag(notification_id: str) -> None:
    redis_client = get_redis_client()
    if redis_client is None:
        return
    try:
        await redis_client.delete(_cancel_key(notification_id))
    except Exception as e:
        logger.warning(f"Failed to clear cancel flag for {notification_id}: {e}")


async def run_reindex(notification_id: str) -> None:
    """
    Re-embed every row in knowledge_store against the saved embedding config.

    Pushes progress through NotificationService.update_notification (which
    broadcasts on the WebSocket notification:{user_id} channel). The caller
    is responsible for creating the notification first.

    Errors mid-job leave partial state and flip the notification to FAILED.
    Cancellation leaves partial state and flips it to CANCELLED.
    """
    notif_service = get_notification_service()
    await clear_cancel_flag(notification_id)

    processed = 0
    total = 0
    failed_batches = 0

    try:
        async with get_db_context() as db:
            # Count first so we can compute progress percent and surface
            # "no rows" as an immediate completed-with-zero state.
            total_result = await db.execute(
                select(KnowledgeStore.id).execution_options(yield_per=None)
            )
            row_ids = [row[0] for row in total_result.all()]
            total = len(row_ids)

            await notif_service.update_notification(
                notification_id,
                NotificationUpdate(
                    status=NotificationStatus.RUNNING,
                    description=f"Re-embedding {total} rows...",
                    percent=0.0 if total > 0 else 100.0,
                ),
            )

            if total == 0:
                await notif_service.update_notification(
                    notification_id,
                    NotificationUpdate(
                        status=NotificationStatus.COMPLETED,
                        description="No knowledge store rows to reindex.",
                        percent=100.0,
                        result={"processed": 0, "total": 0, "failed_batches": 0},
                    ),
                )
                return

            client = await get_embedding_client(db)

            # Embedding API calls happen in batches (round-trip efficiency).
            # Progress notifications fire per-row so the UI sees real-time motion.
            # Cancellation is checked between embed-batches.
            for batch_start in range(0, total, EMBED_BATCH_SIZE):
                if await is_cancelled(notification_id):
                    await notif_service.update_notification(
                        notification_id,
                        NotificationUpdate(
                            status=NotificationStatus.CANCELLED,
                            description=(
                                f"Cancelled after {processed}/{total} rows. "
                                "Partial state retained."
                            ),
                            result={
                                "processed": processed,
                                "total": total,
                                "failed_batches": failed_batches,
                                "cancelled": True,
                            },
                        ),
                    )
                    return

                batch_ids = row_ids[batch_start : batch_start + EMBED_BATCH_SIZE]

                # Pull the content for this batch.
                rows_result = await db.execute(
                    select(KnowledgeStore.id, KnowledgeStore.content).where(
                        KnowledgeStore.id.in_(batch_ids)
                    )
                )
                batch_rows = rows_result.all()
                if not batch_rows:
                    continue

                texts = [row.content for row in batch_rows]

                try:
                    embeddings = await client.embed(texts)
                except Exception as e:
                    failed_batches += 1
                    logger.error(
                        f"Reindex batch {batch_start}-{batch_start + len(batch_rows)} "
                        f"failed: {e}"
                    )
                    # Skip the batch; the rows keep their old embeddings.
                    processed += len(batch_rows)
                    await _push_progress(
                        notif_service, notification_id, processed, total
                    )
                    continue

                # Update each row, push progress after each. pgvector +
                # SQLAlchemy doesn't have a nice executemany for vector params
                # but round-trips are bounded to a single connection.
                for row, vector in zip(batch_rows, embeddings):
                    await db.execute(
                        update(KnowledgeStore)
                        .where(KnowledgeStore.id == row.id)
                        .values(embedding=vector)
                    )
                    await db.commit()
                    processed += 1
                    await _push_progress(
                        notif_service, notification_id, processed, total
                    )

            await notif_service.update_notification(
                notification_id,
                NotificationUpdate(
                    status=NotificationStatus.COMPLETED,
                    description=(
                        f"Reindexed {processed}/{total} rows."
                        + (
                            f" ({failed_batches} batches failed and were skipped.)"
                            if failed_batches
                            else ""
                        )
                    ),
                    percent=100.0,
                    result={
                        "processed": processed,
                        "total": total,
                        "failed_batches": failed_batches,
                    },
                ),
            )

    except Exception as e:
        logger.exception("Reindex job failed")
        await notif_service.update_notification(
            notification_id,
            NotificationUpdate(
                status=NotificationStatus.FAILED,
                error=str(e),
                description=f"Reindex failed after {processed}/{total} rows.",
                result={
                    "processed": processed,
                    "total": total,
                    "failed_batches": failed_batches,
                },
            ),
        )
    finally:
        await clear_cancel_flag(notification_id)


async def _push_progress(
    notif_service, notification_id: str, processed: int, total: int
) -> None:
    percent = (processed / total * 100.0) if total else 100.0
    await notif_service.update_notification(
        notification_id,
        NotificationUpdate(
            status=NotificationStatus.RUNNING,
            description=f"Re-embedded {processed}/{total} rows...",
            percent=percent,
        ),
    )


async def count_knowledge_rows() -> int:
    """Tiny helper used by the API layer to decide whether reindex is needed."""
    from sqlalchemy import func

    async with get_db_context() as db:
        result = await db.execute(select(func.count(KnowledgeStore.id)))
        return cast(int, result.scalar_one())


__all__ = [
    "EMBED_BATCH_SIZE",
    "run_reindex",
    "is_cancelled",
    "mark_cancelled",
    "clear_cancel_flag",
    "count_knowledge_rows",
]
