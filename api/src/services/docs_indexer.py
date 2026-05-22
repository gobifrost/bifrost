"""
Documentation Indexer Service

Indexes bundled platform documentation into the knowledge store on startup.
Enables the Coding Agent to search Bifrost docs for accurate SDK guidance.

Uses content hashing to avoid unnecessary OpenAI API calls - only re-indexes
documents whose content has actually changed since the last indexing run.
"""

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.database import get_db_context
from src.repositories.knowledge import KnowledgeRepository
from src.services.embeddings.factory import get_embedding_client

logger = logging.getLogger(__name__)


def compute_content_hash(content: str) -> str:
    """Compute SHA-256 hash of content for change detection."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

# Path to bundled documentation (in shared/ which is mounted as /app/shared)
DOCS_PATH = Path(__file__).parent.parent.parent / "shared" / "docs"

# Knowledge store namespace for platform docs
NAMESPACE = "bifrost-docs"


async def index_docs_background() -> None:
    """
    Index platform documentation as a background task.

    Called on API startup. Safe to run on every startup - uses upsert
    for idempotency and cleans up orphaned documents.

    This is a fire-and-forget task that logs its own errors.
    """
    try:
        result = await index_platform_docs()
        if result["status"] == "complete":
            logger.info(
                f"Documentation indexing complete: "
                f"{result['indexed']} indexed, {result.get('skipped', 0)} unchanged, "
                f"{result['deleted']} orphaned deleted"
            )
        elif result["status"] == "skipped":
            logger.info(f"Documentation indexing skipped: {result.get('reason', 'unknown')}")
        else:
            logger.warning(f"Documentation indexing result: {result}")
    except Exception as e:
        # Don't fail startup if docs indexing fails
        logger.error(f"Documentation indexing failed: {e}", exc_info=True)


async def index_platform_docs() -> dict[str, Any]:
    """
    Index all platform documentation into the knowledge store.

    Process:
    1. Fetch existing docs and their content hashes
    2. Read all .txt files from api/docs/
    3. Compare content hashes - only re-index changed files
    4. Delete orphaned documents (files that no longer exist)

    Uses content hashing to avoid unnecessary OpenAI API calls.

    Returns:
        Summary dict with status, counts, and any errors
    """
    if not DOCS_PATH.exists():
        return {"status": "skipped", "reason": "docs directory not found"}

    doc_files = list(DOCS_PATH.rglob("*.txt"))
    if not doc_files:
        return {"status": "skipped", "reason": "no documentation files found"}

    indexed_at = datetime.now(timezone.utc).isoformat()
    current_keys: set[str] = set()  # All keys from current files (for orphan detection)
    indexed_count = 0
    skipped_count = 0
    errors: list[str] = []

    # Phase 1: Load existing docs and get embedding client config (short-lived session)
    async with get_db_context() as db:
        try:
            embedding_client = await get_embedding_client(db)
        except ValueError as e:
            return {"status": "skipped", "reason": str(e)}

        repo = KnowledgeRepository(db, org_id=None, is_superuser=True)
        existing_docs = await repo.get_all_by_namespace(namespace=NAMESPACE)

    logger.info(
        f"Checking {len(doc_files)} documentation files "
        f"({len(existing_docs)} already indexed)..."
    )

    # Phase 2: Read files, compute hashes, generate embeddings (no DB connection held)
    docs_to_upsert: list[dict] = []
    for doc_file in doc_files:
        try:
            content = doc_file.read_text(encoding="utf-8")
            key = str(doc_file.relative_to(DOCS_PATH)).replace(".txt", "")
            current_keys.add(key)

            content_hash = compute_content_hash(content)

            existing_doc = existing_docs.get(key)
            if existing_doc:
                existing_hash = existing_doc.metadata.get("content_hash")
                if existing_hash == content_hash:
                    skipped_count += 1
                    continue

            docs_to_upsert.append({
                "content": content,
                "key": key,
                "content_hash": content_hash,
            })
            indexed_count += 1

        except Exception as e:
            error_msg = f"Failed to index {doc_file.name}: {e}"
            logger.error(error_msg)
            errors.append(error_msg)

    # Phase 3: Batch upsert embeddings and cleanup orphans (short-lived session)
    deleted_count = 0
    async with get_db_context() as db:
        repo = KnowledgeRepository(db, org_id=None, is_superuser=True)

        for doc in docs_to_upsert:
            await repo.store_chunked(
                content=doc["content"],
                namespace=NAMESPACE,
                key=doc["key"],
                metadata={
                    "source": "bifrost-docs",
                    "path": doc["key"],
                    "indexed_at": indexed_at,
                    "content_hash": doc["content_hash"],
                },
                embedder=embedding_client,
            )

        if current_keys:
            try:
                deleted_count = await repo.delete_orphaned_docs(
                    namespace=NAMESPACE,
                    valid_keys=current_keys,
                )
                if deleted_count > 0:
                    logger.info(f"Deleted {deleted_count} orphaned documentation entries")
            except Exception as e:
                error_msg = f"Failed to cleanup orphaned docs: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

        await db.commit()

    return {
        "status": "complete",
        "indexed": indexed_count,
        "skipped": skipped_count,
        "deleted": deleted_count,
        "errors": errors if errors else None,
        "namespace": NAMESPACE,
    }


async def reindex_docs() -> dict[str, Any]:
    """
    Force reindex all documentation.

    Useful for manual refresh via admin API or CLI.
    Same as index_platform_docs() but meant for explicit invocation.
    """
    return await index_platform_docs()
