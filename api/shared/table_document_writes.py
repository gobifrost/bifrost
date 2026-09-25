"""Shared table document write service.

HTTP handlers in ``src.routers.tables`` and the future engine-parent local
dispatcher call the same functions here so mutation status, policy, commit,
and publication behavior stay identical across transports.

Table resolution (``shared.table_resolution.get_table_or_404``), the Solution
write-target gate, and the batch explicit-scope exact-table gate stay outside
this module — callers resolve and gate the :class:`Table` first, then call
into this module.

Errors are transport-neutral :class:`TableWriteError` subclasses carrying an
HTTP-equivalent ``status_code`` plus the existing ``detail`` payload. Thin
HTTP adapters catch them and raise ``HTTPException`` with the same status
and detail; the future local dispatcher maps them to its own error shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from shared.claims.preresolve import preresolve_for_policies
from shared.policies.probe import evaluate_action
from shared.table_batch_writes import (
    BatchPolicyDenied,
    BatchWriteMode,
    BatchWriteRow,
    ConcurrentBatchWrite,
    DuplicateBatchIds,
    InsertConflict,
    _row_from_doc,
    _update_post_image_row,
    write_table_batch,
)
from shared.table_documents import (
    DocumentRepository,
    check_table_action_or_403,
)
from src.core.constants import SYSTEM_USER_UUID
from src.core.principal import UserPrincipal
from src.models.orm.tables import Document, Table
from src.services.audit import emit_table_policy_deny
from src.services.table_policy_loader import load_resolved_table_policies
from src.core.pubsub import (
    publish_document_change,
    publish_table_invalidated,
)


class TableWriteError(Exception):
    """Transport-neutral write failure with an HTTP-equivalent status."""

    status_code: int = 500

    def __init__(self, detail: Any = "Internal error") -> None:
        self.detail = detail
        super().__init__(str(detail))


class TableWriteNotFound(TableWriteError):
    status_code = 404


class TableWriteForbidden(TableWriteError):
    status_code = 403


class TableWriteUnprocessable(TableWriteError):
    status_code = 422


class TableWriteConflict(TableWriteError):
    status_code = 409


def resolve_attribution(
    user: UserPrincipal,
    body_created_by: str | None,
    body_updated_by: str | None,
) -> tuple[str, str]:
    """Decide attribution (created_by, updated_by) for a document write.

    If the body carries either field, the caller must be the engine
    (SYSTEM_USER_UUID) or a platform admin (is_superuser); otherwise raise
    :class:`TableWriteForbidden` so a regular user can't forge attribution.

    Defaulting:
    - both omitted → both default to the caller's id.
    - only created_by provided → updated_by mirrors it (same actor on first write).
    - only updated_by provided → created_by defaults to the caller (only meaningful
      on insert; ignored on the update path).
    """
    has_override = body_created_by is not None or body_updated_by is not None
    if has_override:
        is_engine = user.user_id == SYSTEM_USER_UUID
        if not (is_engine or user.is_superuser):
            raise TableWriteForbidden(
                "created_by/updated_by override requires engine or platform-admin caller"
            )
    caller = str(user.user_id)
    created_by = body_created_by or caller
    updated_by = body_updated_by or body_created_by or caller
    return (created_by, updated_by)


async def require_update_policy(
    table: Table,
    old_row: dict[str, Any],
    new_row: dict[str, Any],
    user: UserPrincipal,
    *,
    db: AsyncSession,
) -> None:
    """Require the ``update`` policy on BOTH pre-image and post-image.

    Same contract as the former router-local ``_check_update_or_403``: a
    single ``policy.deny`` audit row on either failure, generic 403 detail,
    and no uncommitted caller mutations allowed at call time.
    """
    policies = await load_resolved_table_policies(table, db)
    await preresolve_for_policies(
        user,
        policies,
        db,
        table.organization_id,
        table.solution_id,
    )
    if evaluate_action("update", policies, old_row, user) and evaluate_action(
        "update", policies, new_row, user
    ):
        return

    raw_id = old_row.get("id")
    resource_id: UUID | None = None
    if raw_id is not None:
        try:
            resource_id = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))
        except (ValueError, TypeError):
            resource_id = None

    await emit_table_policy_deny(
        db,
        policy_action="update",
        table_id=table.id,
        table_name=table.name,
        resource_id=resource_id,
    )
    await db.commit()
    raise TableWriteForbidden("Access denied")


async def _check_action(
    action: str,
    table: Table,
    row: dict[str, Any],
    user: UserPrincipal,
    *,
    db: AsyncSession,
) -> None:
    """Run the shared generic policy gate, mapping HTTP 403 to neutral."""
    try:
        await check_table_action_or_403(action, table, row, user, db=db)
    except TableWriteError:
        raise
    except Exception as exc:  # HTTPException from the shared gate
        status_code = getattr(exc, "status_code", None)
        if status_code == 403:
            raise TableWriteForbidden(getattr(exc, "detail", "Access denied")) from exc
        raise


async def insert_table_document(
    db: AsyncSession,
    table: Table,
    user: UserPrincipal,
    *,
    doc_id: str | None,
    data: dict[str, Any],
    created_by: str | None,
    updated_by: str | None,
    upsert: bool = False,
) -> Document:
    """Insert a document, or merge-update it on the legacy upsert branch.

    Preserves the ``DocumentCreate.upsert`` branch: when ``upsert`` is true
    and ``doc_id`` names an existing row, the row is merge-updated (PATCH
    semantics) after checking ``update`` against both pre-image and merged
    post-image. Otherwise the candidate row is ``create``-checked and
    inserted. Commits before publishing one ``insert``/``update`` event.
    """
    repo = DocumentRepository(db, table)
    resolved_created_by, resolved_updated_by = resolve_attribution(
        user, created_by, updated_by
    )

    if upsert and doc_id:
        existing = await repo.get(doc_id)
        if existing is not None:
            old_row = _row_from_doc(existing)
            new_row = _update_post_image_row(
                old_row,
                data,
                updated_by=resolved_updated_by,
                now=datetime.now(timezone.utc),
                replace=False,
            )
            await require_update_policy(table, old_row, new_row, user, db=db)
            doc = await repo.update(doc_id, data, updated_by=resolved_updated_by)
            if doc is None:
                raise TableWriteNotFound("Document not found")
            await db.commit()
            await publish_document_change(
                table_id=str(table.id),
                action="update",
                old_row=old_row,
                new_row=_row_from_doc(doc),
            )
            return doc

    candidate_row: dict[str, Any] = {
        **data,
        "id": doc_id,
        "created_by": resolved_created_by,
        "updated_by": resolved_updated_by,
    }
    await _check_action("create", table, candidate_row, user, db=db)
    doc = await repo.insert(
        data,
        created_by=resolved_created_by,
        doc_id=doc_id,
        updated_by=resolved_updated_by,
    )
    await db.commit()
    await publish_document_change(
        table_id=str(table.id),
        action="insert",
        old_row=None,
        new_row=_row_from_doc(doc),
    )
    return doc


async def upsert_table_document(
    db: AsyncSession,
    table: Table,
    user: UserPrincipal,
    *,
    doc_id: str,
    data: dict[str, Any],
    created_by: str | None,
    updated_by: str | None,
) -> Document:
    """Atomically replace-upsert a document by id.

    On conflict the JSONB ``data`` column is replaced, not merged. The
    candidate row is ``create``-checked; when a row already exists,
    ``update`` is additionally required on both its pre-image and the
    replaced post-image. Commits before publishing one event.
    """
    repo = DocumentRepository(db, table)
    resolved_created_by, resolved_updated_by = resolve_attribution(
        user, created_by, updated_by
    )

    existing = await repo.get(doc_id)
    old_row: dict[str, Any] | None = None
    if existing is not None:
        old_row = _row_from_doc(existing)
        new_row = _update_post_image_row(
            old_row,
            data,
            updated_by=resolved_updated_by,
            now=datetime.now(timezone.utc),
            replace=True,
        )
        await require_update_policy(table, old_row, new_row, user, db=db)
    candidate_row: dict[str, Any] = {
        **data,
        "id": doc_id,
        "created_by": resolved_created_by,
        "updated_by": resolved_updated_by,
    }
    await _check_action("create", table, candidate_row, user, db=db)

    doc, inserted = await repo.upsert(
        doc_id, data, created_by=resolved_created_by, updated_by=resolved_updated_by
    )
    await db.commit()
    await publish_document_change(
        table_id=str(table.id),
        action="insert" if inserted else "update",
        old_row=None if inserted else old_row,
        new_row=_row_from_doc(doc),
    )
    return doc


async def update_table_document(
    db: AsyncSession,
    table: Table,
    user: UserPrincipal,
    *,
    doc_id: str,
    data: dict[str, Any],
    updated_by: str | None,
) -> Document:
    """Partial-merge update of a document.

    The ``update`` policy must pass on both pre-image and merged post-image.
    Missing rows raise :class:`TableWriteNotFound`, including a lost race
    with a concurrent delete after fetch + access check.
    """
    repo = DocumentRepository(db, table)
    _, resolved_updated_by = resolve_attribution(user, None, updated_by)
    existing = await repo.get(doc_id)
    if existing is None:
        raise TableWriteNotFound("Document not found")
    old_row = _row_from_doc(existing)
    new_row = _update_post_image_row(
        old_row,
        data,
        updated_by=resolved_updated_by,
        now=datetime.now(timezone.utc),
        replace=False,
    )
    await require_update_policy(table, old_row, new_row, user, db=db)
    doc = await repo.update(doc_id, data, updated_by=resolved_updated_by)
    if doc is None:
        raise TableWriteNotFound("Document not found")
    await db.commit()
    await publish_document_change(
        table_id=str(table.id),
        action="update",
        old_row=old_row,
        new_row=_row_from_doc(doc),
    )
    return doc


async def delete_table_document(
    db: AsyncSession,
    table: Table,
    user: UserPrincipal,
    *,
    doc_id: str,
) -> bool:
    """Delete a document after a ``delete`` policy check.

    Missing rows raise :class:`TableWriteNotFound`. Commits before
    publishing one ``delete`` event when a row was removed.
    """
    repo = DocumentRepository(db, table)
    existing = await repo.get(doc_id)
    if existing is None:
        raise TableWriteNotFound("Document not found")
    old_row = _row_from_doc(existing)
    await _check_action("delete", table, old_row, user, db=db)
    deleted = await repo.delete(doc_id)
    await db.commit()
    if deleted:
        await publish_document_change(
            table_id=str(table.id),
            action="delete",
            old_row=old_row,
            new_row=None,
        )
    return deleted


@dataclass(frozen=True)
class BatchDocumentInput:
    id: str | None
    data: dict[str, Any]
    created_by: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class BatchWriteOutcome:
    ordered_documents: list[Document]
    insert_conflicts: list[InsertConflict]
    inserted: int


@dataclass(frozen=True)
class BatchDeleteOutcome:
    deleted: int
    deleted_ids: list[str]


async def batch_write_table_documents(
    db: AsyncSession,
    table: Table,
    user: UserPrincipal,
    *,
    items: Sequence[BatchDocumentInput],
    mode: BatchWriteMode,
) -> BatchWriteOutcome:
    """Insert, merge-upsert, or replace-upsert a batch of documents.

    Owns attribution resolution per row, batch policy loading +
    preresolution, ``DuplicateBatchIds``/``BatchPolicyDenied``/
    ``ConcurrentBatchWrite`` mapping (preserving 422/403/409 details),
    commit, and single ``table_invalidated`` publication when state changed.
    Successful documents preserve submission order.
    """
    policies = await load_resolved_table_policies(table, db)
    await preresolve_for_policies(
        user,
        policies,
        db,
        table.organization_id,
        table.solution_id,
    )

    rows: list[BatchWriteRow] = []
    for index, item in enumerate(items):
        created_by, updated_by = resolve_attribution(
            user, item.created_by, item.updated_by
        )
        rows.append(
            BatchWriteRow(
                submission_index=index,
                id=item.id,
                data=item.data,
                created_by=created_by,
                updated_by=updated_by,
            )
        )

    try:
        result = await write_table_batch(
            db,
            table,
            rows,
            mode=mode,
            policies=policies,
            user=user,
        )
    except DuplicateBatchIds as exc:
        raise TableWriteUnprocessable({"duplicate_ids": exc.ids}) from exc
    except BatchPolicyDenied as exc:
        raise TableWriteForbidden({"denied_row_indices": exc.indices}) from exc
    except ConcurrentBatchWrite as exc:
        await db.rollback()
        raise TableWriteConflict(
            "Batch write conflicted with a concurrent insert; retry the request"
        ) from exc

    ordered_documents = [
        result.documents_by_index[row.submission_index]
        for row in rows
        if row.submission_index in result.documents_by_index
    ]

    await db.commit()
    if ordered_documents:
        await publish_table_invalidated(str(table.id))
    return BatchWriteOutcome(
        ordered_documents=ordered_documents,
        insert_conflicts=result.insert_conflicts,
        inserted=len(ordered_documents),
    )


async def batch_delete_table_documents(
    db: AsyncSession,
    table: Table,
    user: UserPrincipal,
    *,
    ids: Sequence[str],
) -> BatchDeleteOutcome:
    """Delete a batch of documents by ID.

    Skips IDs that don't exist. All-or-nothing on policy denials: any
    denied existing row aborts the whole batch with a 403 listing every
    denied index. Commits once and publishes a single invalidation when at
    least one row was removed. Deleted IDs return in input order.
    """
    repo = DocumentRepository(db, table)
    policies = await load_resolved_table_policies(table, db)
    await preresolve_for_policies(
        user,
        policies,
        db,
        table.organization_id,
        table.solution_id,
    )

    denied: list[int] = []
    existing_by_index: dict[int, Document] = {}
    for i, doc_id in enumerate(ids):
        existing = await repo.get(doc_id)
        if existing is None:
            continue
        existing_by_index[i] = existing
        if not evaluate_action("delete", policies, _row_from_doc(existing), user):
            denied.append(i)

    if denied:
        raise TableWriteForbidden({"denied_row_indices": denied})

    deleted = 0
    deleted_ids: list[str] = []
    for i, doc_id in enumerate(ids):
        existing = existing_by_index.get(i)
        if existing is None:
            continue
        ok = await repo.delete(doc_id)
        if ok:
            deleted += 1
            deleted_ids.append(doc_id)

    await db.commit()
    if deleted > 0:
        await publish_table_invalidated(str(table.id))
    return BatchDeleteOutcome(deleted=deleted, deleted_ids=deleted_ids)
