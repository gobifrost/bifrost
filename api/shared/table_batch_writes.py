from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.policies.probe import evaluate_action
from src.core.principal import UserPrincipal
from src.models.contracts.policies import TablePolicies
from src.models.orm.tables import Document, Table

BatchWriteMode = Literal["insert", "merge_upsert", "replace_upsert"]


@dataclass(frozen=True)
class BatchWriteRow:
    submission_index: int
    id: str | None
    data: dict[str, Any]
    created_by: str | None
    updated_by: str | None


@dataclass(frozen=True)
class NormalizedBatchWriteRow:
    submission_index: int
    id: str
    data: dict[str, Any]
    created_by: str | None
    updated_by: str | None


@dataclass(frozen=True)
class InsertConflict:
    submission_index: int
    id: str


@dataclass(frozen=True)
class BatchWriteResult:
    documents_by_index: dict[int, Document]
    previous_rows_by_index: dict[int, dict[str, Any]]
    insert_conflicts: list[InsertConflict]


class DuplicateBatchIds(Exception):
    def __init__(self, ids: Sequence[str]):
        self.ids = list(ids)
        super().__init__(f"duplicate document ids in batch: {', '.join(self.ids)}")


class BatchPolicyDenied(Exception):
    def __init__(self, indices: Sequence[int]):
        self.indices = list(indices)
        super().__init__(f"table policies denied rows at indices: {self.indices}")


class ConcurrentBatchWrite(Exception):
    pass


def _row_from_doc(doc: Document) -> dict[str, Any]:
    """Flatten a Document ORM row into the dict shape the evaluator expects."""
    return {
        **(doc.data or {}),
        "id": doc.id,
        "table_id": str(doc.table_id),
        "created_by": doc.created_by,
        "updated_by": doc.updated_by,
        "created_at": doc.created_at.isoformat() if doc.created_at is not None else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at is not None else None,
    }


def _normalize_rows(rows: Sequence[BatchWriteRow]) -> list[NormalizedBatchWriteRow]:
    return [
        NormalizedBatchWriteRow(
            submission_index=row.submission_index,
            id=row.id if row.id is not None else str(uuid4()),
            data=row.data,
            created_by=row.created_by,
            updated_by=row.updated_by,
        )
        for row in rows
    ]


def _find_duplicate_ids(rows: Sequence[NormalizedBatchWriteRow]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for row in rows:
        if row.id in seen and row.id not in duplicates:
            duplicates.append(row.id)
        seen.add(row.id)
    return duplicates


def _candidate_row(
    table: Table, row: NormalizedBatchWriteRow, now: datetime
) -> dict[str, Any]:
    return {
        **row.data,
        "id": row.id,
        "table_id": str(table.id),
        "created_by": row.created_by,
        "updated_by": row.updated_by if row.updated_by is not None else row.created_by,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


def _update_post_image_row(
    old_row: dict[str, Any],
    patch_data: dict[str, Any],
    *,
    updated_by: str | None,
    now: datetime,
    replace: bool,
) -> dict[str, Any]:
    """Build the post-image row dict for ``update`` policy evaluation.

    ``replace=False`` merges ``patch_data`` over the pre-image (PATCH /
    ``merge_upsert`` semantics); ``replace=True`` uses ``patch_data`` as the
    full new data (single ``upsert`` / ``replace_upsert`` semantics, where
    the JSONB ``data`` column is overwritten, not merged).

    Column-mapped fields (``id``, ``table_id``, ``created_by``,
    ``created_at``) always win over same-named keys in ``patch_data`` —
    mirroring :func:`_row_from_doc`, where the ORM columns overwrite the
    flattened JSONB data. ``created_by``/``created_at`` are preserved from
    the pre-image because updates never rewrite them.
    """
    base: dict[str, Any] = dict(patch_data) if replace else {**old_row, **patch_data}
    base["id"] = old_row["id"]
    base["table_id"] = old_row["table_id"]
    base["created_by"] = old_row.get("created_by")
    base["created_at"] = old_row.get("created_at")
    base["updated_by"] = updated_by
    base["updated_at"] = now.isoformat()
    return base


async def _load_existing_for_update(
    session: AsyncSession,
    table: Table,
    ids: Sequence[str],
) -> dict[str, Document]:
    if not ids:
        return {}

    result = await session.execute(
        select(Document)
        .where(Document.table_id == table.id, Document.id.in_(ids))
        .order_by(Document.table_id, Document.id)
        .with_for_update()
    )
    return {doc.id: doc for doc in result.scalars().all()}


def _check_policies(
    *,
    table: Table,
    rows: Sequence[NormalizedBatchWriteRow],
    mode: BatchWriteMode,
    policies: TablePolicies,
    user: UserPrincipal,
    existing: dict[str, Document],
    now: datetime,
) -> dict[int, dict[str, Any]]:
    denied: list[int] = []
    previous_rows_by_index: dict[int, dict[str, Any]] = {}

    for row in rows:
        existing_doc = existing.get(row.id)
        if mode != "insert" and existing_doc is not None:
            old_row = _row_from_doc(existing_doc)
            previous_rows_by_index[row.submission_index] = old_row
            if not evaluate_action("update", policies, old_row, user):
                denied.append(row.submission_index)
                continue
            # Dual-gate: the post-image must also satisfy `update`. Without
            # this, a user authorized on the pre-image (e.g.
            # row.organization_id == their org) could retarget the row into
            # a value they could never write (e.g. another org's id).
            post_image = _update_post_image_row(
                old_row,
                row.data,
                updated_by=row.updated_by if row.updated_by is not None else row.created_by,
                now=now,
                replace=(mode == "replace_upsert"),
            )
            if not evaluate_action("update", policies, post_image, user):
                denied.append(row.submission_index)
            continue

        if not evaluate_action("create", policies, _candidate_row(table, row, now), user):
            denied.append(row.submission_index)

    if denied:
        raise BatchPolicyDenied(denied)

    return previous_rows_by_index


def _values(
    table: Table, rows: Sequence[NormalizedBatchWriteRow], now: datetime
) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "table_id": table.id,
            "data": row.data,
            "created_by": row.created_by,
            "updated_by": row.updated_by if row.updated_by is not None else row.created_by,
            "created_at": now,
            "updated_at": now,
        }
        for row in sorted(rows, key=lambda item: item.id)
    ]


async def write_table_batch(
    session: AsyncSession,
    table: Table,
    rows: Sequence[BatchWriteRow],
    *,
    mode: BatchWriteMode,
    policies: TablePolicies,
    user: UserPrincipal,
    _after_preflight: Callable[[], Awaitable[None]] | None = None,
) -> BatchWriteResult:
    if not rows:
        return BatchWriteResult(
            documents_by_index={},
            previous_rows_by_index={},
            insert_conflicts=[],
        )

    normalized_rows = _normalize_rows(rows)

    duplicates = _find_duplicate_ids(normalized_rows)
    if duplicates:
        raise DuplicateBatchIds(duplicates)

    rows_by_id = {row.id: row for row in normalized_rows}
    ordered_ids = sorted(rows_by_id)
    existing = await _load_existing_for_update(session, table, ordered_ids)
    now = datetime.now(timezone.utc)
    previous_rows_by_index = _check_policies(
        table=table,
        rows=normalized_rows,
        mode=mode,
        policies=policies,
        user=user,
        existing=existing,
        now=now,
    )

    if _after_preflight is not None:
        await _after_preflight()

    insert_stmt = pg_insert(Document).values(_values(table, normalized_rows, now))

    if mode == "insert":
        stmt = (
            insert_stmt.on_conflict_do_nothing(index_elements=["table_id", "id"])
            .returning(Document)
            .execution_options(populate_existing=True)
        )
    else:
        update_data = (
            Document.data.op("||")(insert_stmt.excluded.data)
            if mode == "merge_upsert"
            else insert_stmt.excluded.data
        )
        stmt = (
            insert_stmt.on_conflict_do_update(
                index_elements=["table_id", "id"],
                set_={
                    "data": update_data,
                    "updated_by": insert_stmt.excluded.updated_by,
                    "updated_at": now,
                },
                where=(
                    (Document.table_id == table.id)
                    & (Document.id.in_(set(existing)))
                ),
            )
            .returning(Document)
            .execution_options(populate_existing=True)
        )

    result = await session.scalars(stmt)
    returned_docs = list(result.all())
    documents_by_index = {
        rows_by_id[doc.id].submission_index: doc for doc in returned_docs
    }

    if mode == "insert":
        inserted_ids = {doc.id for doc in returned_docs}
        insert_conflicts = [
            InsertConflict(submission_index=row.submission_index, id=row.id)
            for row in normalized_rows
            if row.id not in inserted_ids
        ]
    else:
        insert_conflicts = []
        if len(returned_docs) != len(normalized_rows):
            raise ConcurrentBatchWrite(
                "batch upsert conflicted with a concurrent insert; retry the request"
            )

    return BatchWriteResult(
        documents_by_index=documents_by_index,
        previous_rows_by_index=previous_rows_by_index,
        insert_conflicts=insert_conflicts,
    )
