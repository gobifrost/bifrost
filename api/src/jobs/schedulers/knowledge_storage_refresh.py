"""
Knowledge Storage Refresh Scheduler

Computes daily knowledge storage usage per organization and namespace.
Runs daily to populate the knowledge_storage_daily table for usage reporting.
"""

import logging
from datetime import date
from typing import Any

from sqlalchemy import String, case, delete, func, select

from src.core.database import get_session_factory
from src.models.orm import KnowledgeStorageDaily, KnowledgeStore

logger = logging.getLogger(__name__)


async def refresh_knowledge_storage_daily() -> dict[str, Any]:
    """
    Compute today's knowledge storage snapshot.

    Aggregates storage usage from knowledge_store by organization and namespace,
    calculating total size from content, metadata, and vector embeddings.

    Size calculation:
    - Content: octet_length(content)
    - Metadata: octet_length(metadata::text)
    - Embedding: 6144 bytes (1536 floats x 4 bytes per float)

    Returns:
        Summary of the refresh operation
    """
    logger.info("▶ Knowledge storage refresh starting")
    today = date.today()

    try:
        session_factory = get_session_factory()
        async with session_factory() as db:
            # Delete existing rows for today (idempotent re-runs)
            delete_result = await db.execute(
                delete(KnowledgeStorageDaily).where(
                    KnowledgeStorageDaily.snapshot_date == today
                )
            )
            deleted_count = delete_result.rowcount

            # Aggregate storage from knowledge_store
            # Note: metadata is the DB column name, accessed via doc_metadata in ORM
            storage_query = (
                select(
                    KnowledgeStore.organization_id,
                    KnowledgeStore.namespace,
                    func.count(KnowledgeStore.id).label("doc_count"),
                    func.sum(
                        func.coalesce(func.octet_length(KnowledgeStore.content), 0)
                        + func.coalesce(
                            func.octet_length(
                                func.cast(KnowledgeStore.doc_metadata, String)
                            ),
                            0,
                        )
                        + case(
                            (KnowledgeStore.embedding.isnot(None), 6144),
                            else_=0,
                        )
                    ).label("size_bytes"),
                )
                .group_by(KnowledgeStore.organization_id, KnowledgeStore.namespace)
            )

            result = await db.execute(storage_query)
            rows_data = result.all()

            # Insert rows for today
            total_docs = 0
            total_bytes = 0
            rows = []

            for row in rows_data:
                doc_count = row.doc_count or 0
                size_bytes = row.size_bytes or 0
                total_docs += doc_count
                total_bytes += size_bytes

                rows.append(
                    KnowledgeStorageDaily(
                        snapshot_date=today,
                        organization_id=row.organization_id,
                        namespace=row.namespace,
                        document_count=doc_count,
                        size_bytes=size_bytes,
                    )
                )

            if rows:
                db.add_all(rows)
                await db.commit()

            size_mb = round(total_bytes / 1048576, 2)
            logger.info(
                f"✓ Knowledge storage refreshed: "
                f"{len(rows)} rows, {total_docs} documents, {size_mb} MB total "
                f"(deleted {deleted_count} old rows)"
            )

            return {
                "date": today.isoformat(),
                "rows": len(rows),
                "total_documents": total_docs,
                "total_size_bytes": total_bytes,
                "total_size_mb": size_mb,
            }

    except Exception as e:
        logger.exception(f"✗ Knowledge storage refresh failed: {e}", exc_info=True)
        return {"error": str(e)}
