"""Durable platform-job adapter for knowledge embedding reindexing."""

from pydantic import BaseModel

from src.jobs.platform.base import (
    PlatformJobCancelled,
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)


class EmbeddingReindexPayload(BaseModel):
    notification_id: str


async def run_embedding_reindex(
    context: PlatformJobContext,
    payload: EmbeddingReindexPayload,
) -> dict:
    from src.services.embeddings.reindex import run_reindex

    await context.report("Re-embedding knowledge store", percent=1)
    outcome = await run_reindex(payload.notification_id)
    if outcome.status == "cancelled":
        raise PlatformJobCancelled
    if outcome.status == "failed":
        raise PlatformJobFailure(
            "embedding_reindex_failed",
            (
                f"Knowledge-store reindex failed after {outcome.processed}/"
                f"{outcome.total} documents."
            ),
        )
    await context.report("Embedding reindex complete", percent=100)
    await context.log(
        "info",
        "embedding_reindex_completed",
        "Knowledge-store embedding reindex completed",
    )
    return {
        "notification_id": payload.notification_id,
        "processed": outcome.processed,
        "total": outcome.total,
        "failed_batches": outcome.failed_batches,
    }


EMBEDDING_REINDEX_DEFINITION = PlatformJobDefinition(
    job_type="embedding.reindex",
    payload_version=1,
    payload_model=EmbeddingReindexPayload,
    handler=run_embedding_reindex,
    policy=PlatformJobPolicy(
        timeout_seconds=4 * 60 * 60,
        max_attempts=2,
        max_concurrency=1,
        min_memory_headroom_mb=512,
    ),
)
