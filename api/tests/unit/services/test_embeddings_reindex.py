"""
Unit tests for the embedding-reindex helpers.

Focus on the cancellation primitives and the no-op short-circuit when the
knowledge store is empty. Full reindex flow (batch embed, UPDATE, progress)
is exercised via an e2e test against real DB + redis.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from src.models.orm.knowledge import KnowledgeStore
from src.services.embeddings import reindex


@pytest.fixture
def mock_redis():
    """A redis client mock with the methods reindex uses."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.setex = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=1)
    return client


@pytest.mark.asyncio
async def test_is_cancelled_returns_false_when_flag_missing(mock_redis):
    mock_redis.get = AsyncMock(return_value=None)
    with patch.object(reindex, "get_redis_client", return_value=mock_redis):
        assert await reindex.is_cancelled("nope") is False


@pytest.mark.asyncio
async def test_is_cancelled_returns_true_when_flag_present(mock_redis):
    mock_redis.get = AsyncMock(return_value="1")
    with patch.object(reindex, "get_redis_client", return_value=mock_redis):
        assert await reindex.is_cancelled("yep") is True


@pytest.mark.asyncio
async def test_is_cancelled_swallows_redis_errors(mock_redis):
    mock_redis.get = AsyncMock(side_effect=RuntimeError("redis exploded"))
    with patch.object(reindex, "get_redis_client", return_value=mock_redis):
        # Cancellation must never raise — we want the reindex to keep going.
        assert await reindex.is_cancelled("err") is False


@pytest.mark.asyncio
async def test_is_cancelled_handles_no_redis_client():
    with patch.object(reindex, "get_redis_client", return_value=None):
        assert await reindex.is_cancelled("x") is False


@pytest.mark.asyncio
async def test_mark_cancelled_writes_flag_with_ttl(mock_redis):
    with patch.object(reindex, "get_redis_client", return_value=mock_redis):
        await reindex.mark_cancelled("notif-1")
    mock_redis.setex.assert_awaited_once_with(
        "bifrost:notification:notif-1:cancelled", 3600, "1"
    )


@pytest.mark.asyncio
async def test_clear_cancel_flag(mock_redis):
    with patch.object(reindex, "get_redis_client", return_value=mock_redis):
        await reindex.clear_cancel_flag("notif-1")
    mock_redis.delete.assert_awaited_once_with(
        "bifrost:notification:notif-1:cancelled"
    )


@pytest.mark.asyncio
async def test_run_reindex_for_group_rechunks_legacy_giant_row(db_session):
    legacy = KnowledgeStore(
        namespace="halo_kb",
        organization_id=None,
        key="legacy-1",
        content=("Long article content. " * 500).strip(),
        doc_metadata={"client_id": "acme"},
        embedding=[0.1] * 8,
        chunk_index=0,
        chunk_count=1,
    )
    db_session.add(legacy)
    await db_session.flush()

    class _Embedder:
        async def embed(self, texts):
            return [[0.2] * 8 for _ in texts]

        async def embed_single(self, text):
            return [0.2] * 8

    await reindex.run_reindex_for_group(
        db_session,
        _Embedder(),
        namespace="halo_kb",
        organization_id=None,
        key="legacy-1",
    )

    rows = (
        await db_session.execute(
            select(KnowledgeStore)
            .where(KnowledgeStore.key == "legacy-1")
            .order_by(KnowledgeStore.chunk_index)
        )
    ).scalars().all()
    assert len(rows) >= 4
    assert all(row.chunk_count == len(rows) for row in rows)
    assert all(row.doc_metadata == {"client_id": "acme"} for row in rows)


@pytest.mark.asyncio
async def test_run_reindex_completes_immediately_when_no_rows():
    """An empty knowledge store should flip the notification to COMPLETED with
    processed=0, not blow up trying to embed nothing."""
    notif_service = MagicMock()
    notif_service.update_notification = AsyncMock()

    db = AsyncMock()
    # The select(KnowledgeStore.id) call returns an empty list.
    empty_result = MagicMock()
    empty_result.all = MagicMock(return_value=[])
    db.execute = AsyncMock(return_value=empty_result)

    db_ctx = AsyncMock()
    db_ctx.__aenter__ = AsyncMock(return_value=db)
    db_ctx.__aexit__ = AsyncMock(return_value=None)

    with (
        patch.object(reindex, "get_notification_service", return_value=notif_service),
        patch.object(reindex, "get_db_context", return_value=db_ctx),
        patch.object(reindex, "clear_cancel_flag", AsyncMock()),
        patch.object(reindex, "is_cancelled", AsyncMock(return_value=False)),
    ):
        await reindex.run_reindex("nid")

    # Last update should have status=completed.
    final_call = notif_service.update_notification.await_args_list[-1]
    final_update = final_call.args[1]
    assert final_update.status is not None
    assert final_update.status.value == "completed"
    assert final_update.percent == 100.0


@pytest.mark.asyncio
async def test_run_reindex_bails_on_cancellation_before_first_batch():
    """Cancellation set before any batch processes should mark CANCELLED with
    processed=0 and never call the embedding client."""
    notif_service = MagicMock()
    notif_service.update_notification = AsyncMock()

    db = AsyncMock()
    groups_result = MagicMock()
    groups_result.all = MagicMock(
        return_value=[("ns", None, f"key-{i}") for i in range(5)]
    )
    keyless_result = MagicMock()
    keyless_result.all = MagicMock(return_value=[])
    db.execute = AsyncMock(side_effect=[groups_result, keyless_result])

    db_ctx = AsyncMock()
    db_ctx.__aenter__ = AsyncMock(return_value=db)
    db_ctx.__aexit__ = AsyncMock(return_value=None)

    embedding_client = MagicMock()
    embedding_client.embed = AsyncMock()

    with (
        patch.object(reindex, "get_notification_service", return_value=notif_service),
        patch.object(reindex, "get_db_context", return_value=db_ctx),
        patch.object(reindex, "clear_cancel_flag", AsyncMock()),
        patch.object(reindex, "is_cancelled", AsyncMock(return_value=True)),
        patch.object(
            reindex, "get_embedding_client", AsyncMock(return_value=embedding_client)
        ),
    ):
        await reindex.run_reindex("nid")

    # Embedding client should never have been called — we bailed immediately.
    embedding_client.embed.assert_not_awaited()

    final_call = notif_service.update_notification.await_args_list[-1]
    final_update = final_call.args[1]
    assert final_update.status is not None
    assert final_update.status.value == "cancelled"


# --- Terminal-status tests for issue #198 ---
#
# Before the fix, a per-batch failure incremented `processed` and the
# terminal notification fired COMPLETED regardless of failure count. These
# tests pin the new behavior:
#   - all batches failed   → FAILED, processed=0
#   - some batches failed  → COMPLETED, processed = succeeded * batch_rows,
#                            description mentions failures
#   - all batches succeeded → COMPLETED (existing behavior, unchanged)


@pytest.mark.asyncio
async def test_run_reindex_marks_failed_when_every_batch_fails():
    """Repro of the prod incident: 23 batches, all failed, UI claimed success."""
    notif_service = MagicMock()
    notif_service.update_notification = AsyncMock()

    db = AsyncMock()
    groups_result = MagicMock()
    groups_result.all = MagicMock(
        return_value=[("ns", None, f"key-{i}") for i in range(3)]
    )
    keyless_result = MagicMock()
    keyless_result.all = MagicMock(return_value=[])
    db.execute = AsyncMock(side_effect=[groups_result, keyless_result])

    db_ctx = AsyncMock()
    db_ctx.__aenter__ = AsyncMock(return_value=db)
    db_ctx.__aexit__ = AsyncMock(return_value=None)

    embedding_client = MagicMock()
    embedding_client.embed = AsyncMock(side_effect=RuntimeError("provider broken"))

    with (
        patch.object(reindex, "get_notification_service", return_value=notif_service),
        patch.object(reindex, "get_db_context", return_value=db_ctx),
        patch.object(reindex, "clear_cancel_flag", AsyncMock()),
        patch.object(reindex, "is_cancelled", AsyncMock(return_value=False)),
        patch.object(
            reindex, "get_embedding_client", AsyncMock(return_value=embedding_client)
        ),
        patch.object(
            reindex,
            "run_reindex_for_group",
            AsyncMock(side_effect=RuntimeError("provider broken")),
        ),
    ):
        await reindex.run_reindex("nid")

    final_call = notif_service.update_notification.await_args_list[-1]
    final_update = final_call.args[1]
    assert final_update.status.value == "failed", (
        "All-batches-failed must report FAILED, not COMPLETED — that was "
        "the issue #198 lie."
    )
    # No row was actually rewritten.
    assert final_update.result["processed"] == 0
    assert final_update.result["failed_batches"] == 3
    assert final_update.result["total_batches"] == 3


@pytest.mark.asyncio
async def test_run_reindex_partial_failure_completes_with_failed_batch_count():
    """One batch fails, two succeed → COMPLETED, but result carries the count."""
    notif_service = MagicMock()
    notif_service.update_notification = AsyncMock()

    db = AsyncMock()
    groups_result = MagicMock()
    groups_result.all = MagicMock(
        return_value=[("ns", None, f"key-{i}") for i in range(3)]
    )
    keyless_result = MagicMock()
    keyless_result.all = MagicMock(return_value=[])
    db.execute = AsyncMock(side_effect=[groups_result, keyless_result])

    db_ctx = AsyncMock()
    db_ctx.__aenter__ = AsyncMock(return_value=db)
    db_ctx.__aexit__ = AsyncMock(return_value=None)

    # Embed: succeed for batch 0 (256 vectors), fail for batch 1, succeed
    # for batch 2 (88 vectors).
    embedding_client = MagicMock()
    embedding_client.embed = AsyncMock(
        side_effect=[
            [[0.0] * 4 for _ in range(256)],
            RuntimeError("provider blip"),
            [[0.0] * 4 for _ in range(88)],
        ]
    )

    with (
        patch.object(reindex, "get_notification_service", return_value=notif_service),
        patch.object(reindex, "get_db_context", return_value=db_ctx),
        patch.object(reindex, "clear_cancel_flag", AsyncMock()),
        patch.object(reindex, "is_cancelled", AsyncMock(return_value=False)),
        patch.object(
            reindex, "get_embedding_client", AsyncMock(return_value=embedding_client)
        ),
        patch.object(
            reindex,
            "run_reindex_for_group",
            AsyncMock(side_effect=[2, RuntimeError("provider blip"), 2]),
        ),
    ):
        await reindex.run_reindex("nid")

    final_call = notif_service.update_notification.await_args_list[-1]
    final_update = final_call.args[1]
    assert final_update.status.value == "completed"
    assert final_update.result["processed"] == 2
    assert final_update.result["failed_batches"] == 1
    assert final_update.result["total_batches"] == 3
    assert final_update.result["total"] == 3
    assert "1/3 document(s) failed" in final_update.description


@pytest.mark.asyncio
async def test_count_knowledge_rows_at_other_dims_uses_vector_dims_filter():
    """Sanity-check the helper: it should grow the WHERE clause with
    `vector_dims(embedding) <> :td` and bind target_dim."""
    db = AsyncMock()
    scalar_result = MagicMock()
    scalar_result.scalar_one = MagicMock(return_value=42)
    db.execute = AsyncMock(return_value=scalar_result)

    db_ctx = AsyncMock()
    db_ctx.__aenter__ = AsyncMock(return_value=db)
    db_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch.object(reindex, "get_db_context", return_value=db_ctx):
        result = await reindex.count_knowledge_rows_at_other_dims(3072)

    assert result == 42
    # Verify the SQL used vector_dims; we look at the compiled string.
    executed_stmt = db.execute.await_args.args[0]
    compiled = str(executed_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "vector_dims" in compiled
    assert "3072" in compiled
