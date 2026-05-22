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
from src.repositories.knowledge import KnowledgeRepository
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


async def run_reindex_for_group(
    db,
    embedder,
    *,
    namespace: str,
    organization_id,
    key: str,
) -> int:
    """
    Re-chunk and re-embed rows under (namespace, organization_id, key).

    Returns the number of chunks written. Cancellation and progress reporting
    are handled by the caller.
    """
    stmt = (
        select(KnowledgeStore)
        .where(
            KnowledgeStore.namespace == namespace,
            KnowledgeStore.key == key,
        )
        .order_by(KnowledgeStore.chunk_index)
    )
    if organization_id is not None:
        stmt = stmt.where(KnowledgeStore.organization_id == organization_id)
    else:
        stmt = stmt.where(KnowledgeStore.organization_id.is_(None))

    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return 0

    full_content = "".join(row.content for row in rows)
    metadata = rows[0].doc_metadata
    created_by = rows[0].created_by

    repo = KnowledgeRepository(db, org_id=organization_id, is_superuser=True)
    new_ids = await repo.store_chunked(
        content=full_content,
        namespace=namespace,
        key=key,
        metadata=metadata,
        organization_id=organization_id,
        created_by=created_by,
        embedder=embedder,
    )
    return len(new_ids)


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
    total_batches = 0

    try:
        async with get_db_context() as db:
            groups_result = await db.execute(
                select(
                    KnowledgeStore.namespace,
                    KnowledgeStore.organization_id,
                    KnowledgeStore.key,
                )
                .where(KnowledgeStore.key.is_not(None))
                .distinct()
            )
            groups = groups_result.all()

            keyless_result = await db.execute(
                select(KnowledgeStore.id, KnowledgeStore.content).where(
                    KnowledgeStore.key.is_(None)
                )
            )
            keyless_rows = keyless_result.all()

            total = len(groups) + len(keyless_rows)

            await notif_service.update_notification(
                notification_id,
                NotificationUpdate(
                    status=NotificationStatus.RUNNING,
                    description=f"Re-embedding {total} knowledge document(s)...",
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

            total_batches = total

            for namespace, org_id, key in groups:
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
                                "total_batches": total_batches,
                                "cancelled": True,
                            },
                        ),
                    )
                    return

                try:
                    await run_reindex_for_group(
                        db,
                        client,
                        namespace=namespace,
                        organization_id=org_id,
                        key=key,
                    )
                    await db.commit()
                except Exception as e:
                    failed_batches += 1
                    logger.error(f"Reindex group {namespace}/{org_id}/{key} failed: {e}")
                    await db.rollback()
                    await _push_progress(
                        notif_service, notification_id, processed, total
                    )
                    continue

                processed += 1
                await _push_progress(
                    notif_service, notification_id, processed, total
                )

            for row in keyless_rows:
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
                                "total_batches": total_batches,
                                "cancelled": True,
                            },
                        ),
                    )
                    return

                try:
                    vector = await client.embed_single(row.content)
                    await db.execute(
                        update(KnowledgeStore)
                        .where(KnowledgeStore.id == row.id)
                        .values(embedding=vector)
                    )
                    await db.commit()
                except Exception as e:
                    failed_batches += 1
                    logger.error(f"Reindex keyless row {row.id} failed: {e}")
                    await db.rollback()
                    await _push_progress(
                        notif_service, notification_id, processed, total
                    )
                    continue

                processed += 1
                await _push_progress(
                    notif_service, notification_id, processed, total
                )

            # Terminal status reflects the real outcome:
            #   all batches failed   → FAILED (no rows rewritten)
            #   some batches failed  → COMPLETED, partial-state message
            #   all batches succeeded → COMPLETED
            if failed_batches and failed_batches >= total_batches:
                await notif_service.update_notification(
                    notification_id,
                    NotificationUpdate(
                        status=NotificationStatus.FAILED,
                        error=(
                            f"All {failed_batches} batches failed; no rows were "
                            "re-embedded. Check scheduler logs and the embedding "
                            "configuration."
                        ),
                        description=f"Reindex failed: 0/{total} rows re-embedded.",
                        result={
                            "processed": processed,
                            "total": total,
                            "failed_batches": failed_batches,
                            "total_batches": total_batches,
                        },
                    ),
                )
            else:
                description = f"Reindexed {processed}/{total} knowledge document(s)."
                if failed_batches:
                    description += (
                        f" ({failed_batches}/{total_batches} document(s) failed; "
                        f"{total - processed} kept their old embedding.)"
                    )
                await notif_service.update_notification(
                    notification_id,
                    NotificationUpdate(
                        status=NotificationStatus.COMPLETED,
                        description=description,
                        percent=100.0,
                        result={
                            "processed": processed,
                            "total": total,
                            "failed_batches": failed_batches,
                            "total_batches": total_batches,
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
            description=f"Re-embedded {processed}/{total} knowledge document(s)...",
            percent=percent,
        ),
    )


async def count_knowledge_rows() -> int:
    """Tiny helper used by the API layer to decide whether reindex is needed."""
    from sqlalchemy import func

    async with get_db_context() as db:
        result = await db.execute(select(func.count(KnowledgeStore.id)))
        return cast(int, result.scalar_one())


async def count_knowledge_rows_at_other_dims(target_dim: int) -> int:
    """
    Return the number of `knowledge_store` rows whose embedding dimension
    is NOT ``target_dim``.

    The embedding-config save gate uses this to ground its reindex prompt
    in actual DB state instead of the persisted config, which closes two
    holes:

    1. **Resave with stale rows.** If the persisted config already says
       3072 but the DB still has 1536-dim rows from before the previous
       (failed) reindex, a config-vs-config diff would say "no change"
       and skip the prompt. A DB-grounded check still fires.
    2. **First save with no recorded ``dimensions``.** Same shape — the
       diff has nothing to compare against and silently passes.
    """
    from sqlalchemy import func, text

    async with get_db_context() as db:
        # vector_dims() is a pgvector function; SQLAlchemy doesn't model it,
        # so we drop down to a literal text expression.
        result = await db.execute(
            select(func.count()).where(
                text("vector_dims(embedding) <> :td").bindparams(td=target_dim)
            ).select_from(KnowledgeStore)
        )
        return cast(int, result.scalar_one())


__all__ = [
    "EMBED_BATCH_SIZE",
    "run_reindex",
    "run_reindex_for_group",
    "is_cancelled",
    "mark_cancelled",
    "clear_cancel_flag",
    "count_knowledge_rows",
    "count_knowledge_rows_at_other_dims",
]
