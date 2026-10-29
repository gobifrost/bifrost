"""
Knowledge Sources Router

Namespace-based knowledge management.
Namespaces are derived from the knowledge_store table.
Documents are stored via the KnowledgeRepository with embeddings.
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import and_, delete, or_, select, true, update

from shared.sdk_knowledge import (
    KnowledgeAuthorizationError,
    require_direct_knowledge_access,
    resolve_knowledge_scope,
)
from src.core.auth import CurrentActiveUser
from src.core.constants import PROVIDER_ORG_ID
from src.core.db_deps import DbSession
from src.models.contracts.knowledge import (
    KnowledgeDocumentBulkScopeUpdate,
    KnowledgeDocumentCreate,
    KnowledgeDocumentPublic,
    KnowledgeDocumentSummary,
    KnowledgeDocumentUpdate,
    KnowledgeNamespaceInfo,
)
from src.models.orm.knowledge import KnowledgeStore
from src.repositories.knowledge import KnowledgeRepository
from src.services.authorization.enforce import (
    Caller,
    load_caller,
    operation_reach,
    org_target,
    permitted_organizations,
    require_operation,
    user_reach,
)
from src.services.authorization.reach import OrgReach
from src.services.operation_catalog import operation_route

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge-sources", tags=["Knowledge Sources"])

KNOWLEDGE_NAMESPACES_LIST_OPERATION = "knowledge.namespaces.list"
KNOWLEDGE_DOCUMENTS_LIST_OPERATION = "knowledge.documents.list"
KNOWLEDGE_DOCUMENTS_SCOPE_UPDATE_OPERATION = "PATCH /api/knowledge-sources/documents/scope"
KNOWLEDGE_NAMESPACE_DOCUMENTS_LIST_OPERATION = "GET /api/knowledge-sources/{namespace}/documents"
KNOWLEDGE_DOCUMENTS_CREATE_OPERATION = "knowledge.documents.create"
KNOWLEDGE_DOCUMENTS_GET_OPERATION = "knowledge.documents.get"
KNOWLEDGE_DOCUMENTS_UPDATE_OPERATION = "knowledge.documents.update"
KNOWLEDGE_DOCUMENTS_DELETE_OPERATION = "knowledge.documents.delete"
KNOWLEDGE_NAMESPACE_DELETE_OPERATION = "DELETE /api/knowledge-sources/{namespace}"


async def _knowledge_caller(db: DbSession, user: CurrentActiveUser) -> Caller:
    """Load original-user authority and retain the authenticated actor for writes."""
    caller = await load_caller(db, user)
    try:
        require_direct_knowledge_access(caller)
    except KnowledgeAuthorizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return caller


async def _authorize_knowledge_scope(
    db: DbSession,
    user: CurrentActiveUser,
    operation: str,
    scope: str | None,
) -> tuple[Caller, UUID | None]:
    """Validate a target against original-user reach, then require its operation."""
    caller = await _knowledge_caller(db, user)
    try:
        organization_id = resolve_knowledge_scope(scope, caller=caller)
    except KnowledgeAuthorizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    require_operation(caller, operation, org_target(organization_id))
    return caller, organization_id


def _scope_with_shared_defaults(organization_id: UUID | None):
    """The selected org and its shared Global defaults, or Global alone."""
    if organization_id is None:
        return KnowledgeStore.organization_id.is_(None)
    return or_(
        KnowledgeStore.organization_id == organization_id,
        KnowledgeStore.organization_id.is_(None),
    )


def _conceal_document_outside_reach(
    reach: OrgReach,
    *,
    organization_id: UUID | None,
    doc_id: UUID,
    namespace: str,
) -> None:
    """Hide foreign organization documents while preserving Global's 403."""
    if organization_id is not None and not reach.covers(organization_id):
        raise HTTPException(404, f"Document {doc_id} not found in namespace {namespace}")


def _has_organization_overlap(left: OrgReach, right: OrgReach) -> bool:
    """Whether two reaches share at least one organization (not Global)."""
    if left.everything:
        return right.everything or right.managed or bool(right.organization_ids)
    if right.everything:
        return left.managed or bool(left.organization_ids)
    if left.organization_ids & right.organization_ids:
        return True
    if left.managed and (
        right.managed
        or any(org_id != PROVIDER_ORG_ID for org_id in right.organization_ids)
    ):
        return True
    return right.managed and any(
        org_id != PROVIDER_ORG_ID for org_id in left.organization_ids
    )


def _list_visibility_clause(caller: Caller):
    """Filter collection reads to permission reach plus shared Global defaults."""
    original_reach = user_reach(caller)
    permission_reach = permitted_organizations(caller, "knowledge.read")
    original_clause = original_reach.where(KnowledgeStore.organization_id)
    permission_clause = permission_reach.where(KnowledgeStore.organization_id)
    clauses = [clause for clause in (original_clause, permission_clause) if clause is not None]
    allowed = and_(*clauses) if clauses else true()
    if permission_reach.covers(None) or _has_organization_overlap(
        original_reach, permission_reach
    ):
        return or_(allowed, KnowledgeStore.organization_id.is_(None))
    return allowed


async def _authorize_knowledge_list(
    db: DbSession,
    user: CurrentActiveUser,
    operation: str,
    scope: str | None,
):
    """Authorize an explicit target or build the reachable collection predicate."""
    if scope is not None and scope != "":
        _, organization_id = await _authorize_knowledge_scope(db, user, operation, scope)
        return _scope_with_shared_defaults(organization_id)
    caller = await _knowledge_caller(db, user)
    operation_reach(caller, operation)
    return _list_visibility_clause(caller)


# =============================================================================
# Namespace Listing
# =============================================================================


@router.get("", **operation_route("knowledge.namespaces.list"))
async def list_namespaces(
    db: DbSession,
    user: CurrentActiveUser,
    scope: str | None = Query(default=None),
) -> list[KnowledgeNamespaceInfo]:
    """List knowledge namespaces derived from knowledge_store."""
    visibility_clause = await _authorize_knowledge_list(
        db, user, KNOWLEDGE_NAMESPACES_LIST_OPERATION, scope
    )
    repo = KnowledgeRepository(session=db)
    ns_list = await repo.list_namespaces(visibility_clause=visibility_clause)

    return [
        KnowledgeNamespaceInfo(
            namespace=ns.namespace,
            document_count=ns.scopes.get("total", 0),
            global_count=ns.scopes.get("global", 0),
            org_count=ns.scopes.get("org", 0),
        )
        for ns in ns_list
    ]


# =============================================================================
# Document listing (all namespaces)
# (Must be registered before /{namespace} routes to avoid path conflicts)
# =============================================================================


@router.get("/documents", **operation_route("knowledge.documents.list"))
async def list_all_documents(
    db: DbSession,
    user: CurrentActiveUser,
    scope: str | None = Query(default=None),
    namespace: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[KnowledgeDocumentSummary]:
    """List all documents across namespaces with optional filters.

    Scope is validated against the original caller's current reach. An
    organization selection includes Global shared defaults.
    """
    visibility_clause = await _authorize_knowledge_list(
        db, user, KNOWLEDGE_DOCUMENTS_LIST_OPERATION, scope
    )

    stmt = select(KnowledgeStore).where(visibility_clause)

    if namespace:
        stmt = stmt.where(KnowledgeStore.namespace == namespace)
    if search:
        stmt = stmt.where(
            KnowledgeStore.content.ilike(f"%{search}%")
            | KnowledgeStore.key.ilike(f"%{search}%")
        )

    stmt = stmt.order_by(KnowledgeStore.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    docs = result.scalars().all()

    return [
        KnowledgeDocumentSummary(
            id=str(d.id),
            namespace=d.namespace,
            key=d.key,
            content_preview=d.content[:200] if d.content else "",
            metadata=d.doc_metadata or {},
            organization_id=str(d.organization_id) if d.organization_id else None,
            created_at=d.created_at,
        )
        for d in docs
    ]


# =============================================================================
# Bulk Document Operations
# =============================================================================


@router.patch("/documents/scope")
async def bulk_update_document_scope(
    data: KnowledgeDocumentBulkScopeUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> dict:
    """Bulk update scope for multiple documents.

    When replace=true in the request body, conflicting documents in the
    target scope are deleted before moving.
    """
    caller, target_org_id = await _authorize_knowledge_scope(
        db, user, KNOWLEDGE_DOCUMENTS_SCOPE_UPDATE_OPERATION, data.scope
    )

    doc_uuids = []
    for did in data.document_ids:
        try:
            doc_uuids.append(UUID(did))
        except ValueError:
            raise HTTPException(422, f"Invalid document ID: {did}")

    # Check for conflicts: docs being moved that have keys matching
    # existing docs in the target scope
    source_docs = await db.execute(
        select(KnowledgeStore).where(KnowledgeStore.id.in_(doc_uuids))
    )
    source_rows = source_docs.scalars().all()
    for organization_id in {row.organization_id for row in source_rows}:
        require_operation(
            caller,
            KNOWLEDGE_DOCUMENTS_SCOPE_UPDATE_OPERATION,
            org_target(organization_id),
        )
    keyed_docs = [
        d for d in source_rows
        if d.key and d.organization_id != target_org_id
    ]

    if keyed_docs:
        keys = [d.key for d in keyed_docs]
        namespaces_set = {d.namespace for d in keyed_docs}
        conflicts = await db.execute(
            select(KnowledgeStore).where(
                KnowledgeStore.namespace.in_(namespaces_set),
                KnowledgeStore.organization_id == target_org_id,
                KnowledgeStore.key.in_(keys),
                ~KnowledgeStore.id.in_(doc_uuids),
            )
        )
        conflicting = conflicts.scalars().all()
        if conflicting:
            if data.replace:
                conflict_ids = [c.id for c in conflicting]
                await db.execute(
                    delete(KnowledgeStore).where(KnowledgeStore.id.in_(conflict_ids))
                )
            else:
                conflict_keys = [f"{c.namespace}/{c.key}" for c in conflicting]
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "conflict",
                        "message": f"{len(conflicting)} document(s) already exist in the target scope with matching keys",
                        "conflicting_keys": conflict_keys,
                    },
                )

    stmt = (
        update(KnowledgeStore)
        .where(KnowledgeStore.id.in_(doc_uuids))
        .values(organization_id=target_org_id, updated_at=datetime.now(timezone.utc))
    )
    result = await db.execute(stmt)
    await db.flush()

    return {"updated": result.rowcount}


# =============================================================================
# Document CRUD (namespace-based paths)
# =============================================================================


@router.get("/{namespace}/documents")
async def list_documents(
    namespace: str,
    db: DbSession,
    user: CurrentActiveUser,
    scope: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[KnowledgeDocumentSummary]:
    """List documents in a namespace."""
    visibility_clause = await _authorize_knowledge_list(
        db, user, KNOWLEDGE_NAMESPACE_DOCUMENTS_LIST_OPERATION, scope
    )

    stmt = select(KnowledgeStore).where(
        KnowledgeStore.namespace == namespace,
        visibility_clause,
    )

    if search:
        stmt = stmt.where(
            KnowledgeStore.content.ilike(f"%{search}%")
            | KnowledgeStore.key.ilike(f"%{search}%")
        )

    stmt = stmt.order_by(KnowledgeStore.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    docs = result.scalars().all()

    return [
        KnowledgeDocumentSummary(
            id=str(d.id),
            namespace=d.namespace,
            key=d.key,
            content_preview=d.content[:200] if d.content else "",
            metadata=d.doc_metadata or {},
            organization_id=str(d.organization_id) if d.organization_id else None,
            created_at=d.created_at,
        )
        for d in docs
    ]


@router.post("/{namespace}/documents", status_code=status.HTTP_201_CREATED, **operation_route("knowledge.documents.create"))
async def create_document(
    namespace: str,
    data: KnowledgeDocumentCreate,
    db: DbSession,
    user: CurrentActiveUser,
    scope: str | None = Query(default=None),
) -> KnowledgeDocumentPublic:
    """Create a document in a namespace with embedding."""
    _, target_org_id = await _authorize_knowledge_scope(
        db, user, KNOWLEDGE_DOCUMENTS_CREATE_OPERATION, scope
    )

    # Generate embedding
    try:
        from src.services.embeddings.factory import get_embedding_client
        client = await get_embedding_client(db)
    except ValueError as e:
        raise HTTPException(503, f"Embedding service unavailable: {e}")

    repo = KnowledgeRepository(session=db, org_id=target_org_id)
    doc_ids = await repo.store_chunked(
        content=data.content,
        namespace=namespace,
        key=data.key,
        metadata=data.metadata,
        organization_id=target_org_id,
        created_by=user.user_id,
        embedder=client,
    )
    await db.flush()

    # Load the created document
    result = await db.execute(
        select(KnowledgeStore).where(KnowledgeStore.id == UUID(doc_ids[0]))
    )
    doc = result.scalar_one()

    return KnowledgeDocumentPublic(
        id=str(doc.id),
        namespace=doc.namespace,
        key=doc.key,
        # Echo the full submitted content, not the first chunk row's slice.
        content=data.content,
        metadata=doc.doc_metadata or {},
        organization_id=str(doc.organization_id) if doc.organization_id else None,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.get("/{namespace}/documents/{doc_id}", **operation_route("knowledge.documents.get"))
async def get_document(
    namespace: str,
    doc_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> KnowledgeDocumentPublic:
    """Get a document by UUID."""
    caller = await _knowledge_caller(db, user)
    reach = operation_reach(caller, KNOWLEDGE_DOCUMENTS_GET_OPERATION)
    repo = KnowledgeRepository(session=db, org_id=None)
    doc = await repo.get_by_id(doc_id)

    if not doc or doc.namespace != namespace:
        raise HTTPException(404, f"Document {doc_id} not found in namespace {namespace}")

    organization_id = UUID(doc.organization_id) if doc.organization_id else None
    # Global remains an explicit permission target: callers with an
    # organization-scoped grant keep the existing 403 rather than treating
    # Global as a hidden organization. Foreign organization documents are
    # hidden before the persisted-target permission check.
    if organization_id is not None and not reach.covers(organization_id):
        raise HTTPException(404, f"Document {doc_id} not found in namespace {namespace}")

    require_operation(
        caller,
        KNOWLEDGE_DOCUMENTS_GET_OPERATION,
        org_target(organization_id),
    )

    return KnowledgeDocumentPublic(
        id=doc.id,
        namespace=doc.namespace,
        key=doc.key,
        content=doc.content,
        metadata=doc.metadata,
        organization_id=doc.organization_id,
        created_at=doc.created_at,
    )


@router.put("/{namespace}/documents/{doc_id}", **operation_route("knowledge.documents.update"))
async def update_document(
    namespace: str,
    doc_id: UUID,
    data: KnowledgeDocumentUpdate,
    db: DbSession,
    user: CurrentActiveUser,
    scope: str | None = Query(default=None),
    replace: bool = Query(default=False),
) -> KnowledgeDocumentPublic:
    """Update a document and re-embed. Optionally change scope.

    Re-embedding goes through the same chunk → embed → store path as create
    (``KnowledgeRepository.replace_chunked``): the document's rows are
    replaced with freshly chunked-and-embedded rows — long content is
    re-chunked into multiple rows, each with a flat per-chunk vector (the
    previous code assigned the whole batch result to a single row's
    ``embedding``, which crashed on ``float()`` and never chunked).

    Identity is stable across edits: the document keeps its id and its
    original ``created_at`` (so edits don't reorder created_at-sorted
    listings). Scope changes are a true *move*: the old rows (in the source
    org) are removed, not left behind as a copy, and a collision with a
    document already holding the same identity in the target scope 409s
    unless ``replace=true``.
    """
    caller = await _knowledge_caller(db, user)
    reach = operation_reach(caller, KNOWLEDGE_DOCUMENTS_UPDATE_OPERATION)

    # First resolve the addressed physical chunk without locking it. Every
    # chunk id is an alias for one logical document, so locking this row would
    # let requests through different chunks deadlock during group replacement.
    result = await db.execute(
        select(KnowledgeStore).where(KnowledgeStore.id == doc_id)
    )
    addressed = result.scalar_one_or_none()
    if not addressed or addressed.namespace != namespace:
        raise HTTPException(404, f"Document {doc_id} not found in namespace {namespace}")
    _conceal_document_outside_reach(
        reach,
        organization_id=addressed.organization_id,
        doc_id=doc_id,
        namespace=namespace,
    )

    # Resolve every alias to chunk zero and lock that persistent canonical row.
    # This gives concurrent edits one lock order and one stable public id.
    source_repo = KnowledgeRepository(session=db, org_id=addressed.organization_id)
    doc = await source_repo.lock_document(
        namespace, addressed.key, addressed.organization_id
    )
    if not doc:
        raise HTTPException(404, f"Document {doc_id} not found in namespace {namespace}")
    _conceal_document_outside_reach(
        reach,
        organization_id=doc.organization_id,
        doc_id=doc_id,
        namespace=namespace,
    )

    # Snapshot identity/audit fields before sibling replacement.
    doc_key = doc.key
    current_org_id = doc.organization_id
    original_created_at = doc.created_at
    original_created_by = doc.created_by
    metadata = data.metadata if data.metadata is not None else (doc.doc_metadata or {})

    require_operation(
        caller,
        KNOWLEDGE_DOCUMENTS_UPDATE_OPERATION,
        org_target(current_org_id),
    )

    # Resolve the target scope (defaults to the doc's current org when unchanged).
    target_org_id = current_org_id
    if scope is not None:
        try:
            target_org_id = resolve_knowledge_scope(scope, caller=caller)
        except KnowledgeAuthorizationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
        require_operation(
            caller,
            KNOWLEDGE_DOCUMENTS_UPDATE_OPERATION,
            org_target(target_org_id),
        )

    repo = KnowledgeRepository(session=db, org_id=target_org_id)

    if target_org_id != current_org_id:
        # Moving scope can collide with a document already holding this
        # identity in the target scope — keyed or keyless (NULL keys are
        # equal under the NULLS NOT DISTINCT unique constraint). 409 before
        # mutating anything, unless the caller asked to replace it.
        conflicting_id = await repo.find_document_id(
            namespace, doc_key, target_org_id
        )
        if conflicting_id:
            if replace:
                await repo.delete_document(namespace, doc_key, target_org_id)
            else:
                descriptor = f"key '{doc_key}'" if doc_key else "no key"
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "conflict",
                        "message": f"A document with {descriptor} already exists in namespace '{namespace}' for the target scope",
                        "conflicting_id": str(conflicting_id),
                        "key": doc_key,
                        "namespace": namespace,
                    },
                )

    try:
        from src.services.embeddings.factory import get_embedding_client
        client = await get_embedding_client(db)
    except ValueError as e:
        raise HTTPException(503, f"Embedding service unavailable: {e}")

    try:
        doc_ids = await repo.replace_chunked(
            doc_id=doc.id,
            content=data.content,
            namespace=namespace,
            key=doc_key,
            current_organization_id=current_org_id,
            organization_id=target_org_id,
            metadata=metadata,
            created_by=original_created_by,
            created_at=original_created_at,
            embedder=client,
        )
    except ValueError as e:
        # Embed-time failures are service unavailability to the caller, same
        # as client construction above. Nothing is lost: replace_chunked only
        # flushes, and the transaction commits in get_db after this handler
        # returns — an error here rolls the whole replace back.
        raise HTTPException(503, f"Embedding service unavailable: {e}")

    stored = await db.execute(
        select(KnowledgeStore).where(KnowledgeStore.id == UUID(doc_ids[0]))
    )
    new_doc = stored.scalar_one()

    return KnowledgeDocumentPublic(
        id=str(new_doc.id),
        namespace=new_doc.namespace,
        key=new_doc.key,
        # Echo the full submitted content, not the first chunk row's slice.
        content=data.content,
        metadata=new_doc.doc_metadata or {},
        organization_id=str(new_doc.organization_id) if new_doc.organization_id else None,
        created_at=new_doc.created_at,
        updated_at=new_doc.updated_at,
    )


@router.delete("/{namespace}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT, **operation_route("knowledge.documents.delete"))
async def delete_document(
    namespace: str,
    doc_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> None:
    """Delete a document — every chunk row of it, not just the addressed row."""
    caller = await _knowledge_caller(db, user)
    reach = operation_reach(caller, KNOWLEDGE_DOCUMENTS_DELETE_OPERATION)
    result = await db.execute(
        select(KnowledgeStore).where(KnowledgeStore.id == doc_id)
    )
    doc = result.scalar_one_or_none()
    if not doc or doc.namespace != namespace:
        raise HTTPException(404, f"Document {doc_id} not found in namespace {namespace}")
    _conceal_document_outside_reach(
        reach,
        organization_id=doc.organization_id,
        doc_id=doc_id,
        namespace=namespace,
    )

    require_operation(
        caller,
        KNOWLEDGE_DOCUMENTS_DELETE_OPERATION,
        org_target(doc.organization_id),
    )

    repo = KnowledgeRepository(session=db, org_id=doc.organization_id)
    await repo.delete_document(namespace, doc.key, doc.organization_id)


@router.delete("/{namespace}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_namespace(
    namespace: str,
    db: DbSession,
    user: CurrentActiveUser,
    scope: str | None = Query(default=None),
) -> None:
    """Delete all documents in a namespace."""
    _, target_org_id = await _authorize_knowledge_scope(
        db, user, KNOWLEDGE_NAMESPACE_DELETE_OPERATION, scope
    )

    repo = KnowledgeRepository(session=db, org_id=target_org_id)
    deleted = await repo.delete_namespace(namespace=namespace, organization_id=target_org_id)

    if deleted == 0:
        raise HTTPException(404, f"Namespace '{namespace}' not found or empty")
