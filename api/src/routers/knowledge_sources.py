"""
Knowledge Sources Router

Namespace-based knowledge management.
Namespaces are derived from the knowledge_store table.
Documents are stored via the KnowledgeRepository with embeddings.
Role assignments use the knowledge_namespace_roles table.
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import delete, select, update

from src.core.auth import CurrentActiveUser, CurrentSuperuser
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.core.org_filter import OrgFilterType, org_filter_clause, resolve_org_filter
from src.models.contracts.knowledge import (
    KnowledgeDocumentBulkScopeUpdate,
    KnowledgeDocumentCreate,
    KnowledgeDocumentPublic,
    KnowledgeDocumentSummary,
    KnowledgeDocumentUpdate,
    KnowledgeNamespaceInfo,
    KnowledgeNamespaceRoleCreate,
    KnowledgeNamespaceRolePublic,
)
from src.models.orm.knowledge import KnowledgeStore
from src.models.orm.knowledge_sources import KnowledgeNamespaceRole
from src.models.orm.users import Role
from src.repositories.knowledge import KnowledgeRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge-sources", tags=["Knowledge Sources"])


def _deny_external(user) -> None:
    """403 an external principal off the direct knowledge surface.

    The knowledge store has no grant axis (no roles, no access_level, no row
    policies), so its read endpoints are implicitly internal-only. Externals
    reach KB content only THROUGH workflows/agents they were granted (the
    engine sentinel keeps the full cascade).
    """
    if getattr(user, "is_external", False):
        raise HTTPException(
            status_code=403,
            detail="External users cannot access the knowledge store directly",
        )


# =============================================================================
# Namespace Listing
# =============================================================================


@router.get("")
async def list_namespaces(
    db: DbSession,
    user: CurrentActiveUser,
    scope: str | None = Query(default=None),
) -> list[KnowledgeNamespaceInfo]:
    """List knowledge namespaces derived from knowledge_store."""
    _deny_external(user)
    try:
        filter_type, filter_org_id = resolve_org_filter(user, scope)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    repo = KnowledgeRepository(session=db, org_id=filter_org_id)

    if filter_type == OrgFilterType.ALL:
        # Superuser with no scope filter — show ALL namespaces
        ns_list = await repo.list_all_namespaces()
    elif filter_type == OrgFilterType.GLOBAL_ONLY:
        ns_list = await repo.list_namespaces(organization_id=None, include_global=True)
    elif filter_type == OrgFilterType.ORG_ONLY:
        ns_list = await repo.list_namespaces(organization_id=filter_org_id, include_global=False)
    else:
        # ORG_PLUS_GLOBAL
        ns_list = await repo.list_namespaces(organization_id=filter_org_id, include_global=True)

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
# Namespace Role Assignments
# (Must be registered before /{namespace} routes to avoid path conflicts)
# =============================================================================


@router.get("/roles")
async def list_namespace_roles(
    db: DbSession,
    user: CurrentSuperuser,
) -> list[KnowledgeNamespaceRolePublic]:
    """List all namespace role assignments."""
    result = await db.execute(select(KnowledgeNamespaceRole))
    assignments = result.scalars().all()

    return [
        KnowledgeNamespaceRolePublic(
            id=str(a.id),
            namespace=a.namespace,
            organization_id=str(a.organization_id) if a.organization_id else None,
            role_id=str(a.role_id),
            assigned_by=a.assigned_by,
        )
        for a in assignments
    ]


@router.post("/roles", status_code=status.HTTP_201_CREATED)
async def assign_namespace_roles(
    data: KnowledgeNamespaceRoleCreate,
    db: DbSession,
    user: CurrentSuperuser,
) -> list[KnowledgeNamespaceRolePublic]:
    """Assign roles to a namespace."""
    org_id = UUID(data.organization_id) if data.organization_id else None
    created = []

    for role_id_str in data.role_ids:
        try:
            role_uuid = UUID(role_id_str)
        except ValueError:
            logger.warning(f"Invalid role ID: {log_safe(role_id_str)}")
            continue

        # Verify role exists
        result = await db.execute(
            select(Role).where(Role.id == role_uuid)
        )
        if not result.scalar_one_or_none():
            continue

        # Check for existing assignment
        existing = await db.execute(
            select(KnowledgeNamespaceRole).where(
                KnowledgeNamespaceRole.namespace == data.namespace,
                KnowledgeNamespaceRole.organization_id == org_id,
                KnowledgeNamespaceRole.role_id == role_uuid,
            )
        )
        if existing.scalar_one_or_none():
            continue

        assignment = KnowledgeNamespaceRole(
            namespace=data.namespace,
            organization_id=org_id,
            role_id=role_uuid,
            assigned_by=user.email,
        )
        db.add(assignment)
        await db.flush()

        created.append(KnowledgeNamespaceRolePublic(
            id=str(assignment.id),
            namespace=assignment.namespace,
            organization_id=str(assignment.organization_id) if assignment.organization_id else None,
            role_id=str(assignment.role_id),
            assigned_by=assignment.assigned_by,
        ))

    return created


@router.delete("/roles/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_namespace_role(
    assignment_id: UUID,
    db: DbSession,
    user: CurrentSuperuser,
) -> None:
    """Remove a namespace role assignment."""
    result = await db.execute(
        select(KnowledgeNamespaceRole).where(KnowledgeNamespaceRole.id == assignment_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(404, f"Assignment {assignment_id} not found")

    await db.execute(
        delete(KnowledgeNamespaceRole).where(KnowledgeNamespaceRole.id == assignment_id)
    )
    await db.flush()


# =============================================================================
# Document listing (all namespaces)
# (Must be registered before /{namespace} routes to avoid path conflicts)
# =============================================================================


@router.get("/documents")
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

    Scope parameter (consistent with workflows, forms, agents):
    - Omitted: show all (superusers only)
    - "global": show only global documents (organization_id IS NULL)
    - UUID string: show only that org's documents (no global fallback)
    """
    _deny_external(user)

    try:
        filter_type, filter_org_id = resolve_org_filter(user, scope)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    stmt = select(KnowledgeStore)

    # Apply org scope filter via the single source of truth — never a
    # hand-rolled cascade (an ``== None`` org filter compiles to ``IS NULL``).
    _clause = org_filter_clause(
        KnowledgeStore.organization_id, filter_type, filter_org_id
    )
    if _clause is not None:
        stmt = stmt.where(_clause)

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
    user: CurrentSuperuser,
) -> dict:
    """Bulk update scope for multiple documents. Superuser only.

    When replace=true in the request body, conflicting documents in the
    target scope are deleted before moving.
    """
    from src.core.org_filter import resolve_target_org
    try:
        target_org_id = resolve_target_org(user, data.scope)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

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
    keyed_docs = [
        d for d in source_docs.scalars().all()
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
    _deny_external(user)

    try:
        filter_type, filter_org_id = resolve_org_filter(user, scope)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    stmt = select(KnowledgeStore).where(KnowledgeStore.namespace == namespace)

    # Org scope via the single source of truth (org_filter_clause).
    _clause = org_filter_clause(
        KnowledgeStore.organization_id, filter_type, filter_org_id
    )
    if _clause is not None:
        stmt = stmt.where(_clause)

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


@router.post("/{namespace}/documents", status_code=status.HTTP_201_CREATED)
async def create_document(
    namespace: str,
    data: KnowledgeDocumentCreate,
    db: DbSession,
    user: CurrentSuperuser,
    scope: str | None = Query(default=None),
) -> KnowledgeDocumentPublic:
    """Create a document in a namespace with embedding."""
    from src.core.org_filter import resolve_target_org
    try:
        target_org_id = resolve_target_org(user, scope)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

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


@router.get("/{namespace}/documents/{doc_id}")
async def get_document(
    namespace: str,
    doc_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> KnowledgeDocumentPublic:
    """Get a document by UUID."""
    _deny_external(user)
    repo = KnowledgeRepository(session=db, org_id=user.organization_id)
    doc = await repo.get_by_id(doc_id)

    if not doc or doc.namespace != namespace:
        raise HTTPException(404, f"Document {doc_id} not found in namespace {namespace}")

    return KnowledgeDocumentPublic(
        id=doc.id,
        namespace=doc.namespace,
        key=doc.key,
        content=doc.content,
        metadata=doc.metadata,
        organization_id=doc.organization_id,
        created_at=doc.created_at,
    )


@router.put("/{namespace}/documents/{doc_id}")
async def update_document(
    namespace: str,
    doc_id: UUID,
    data: KnowledgeDocumentUpdate,
    db: DbSession,
    user: CurrentSuperuser,
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
    # First resolve the addressed physical chunk without locking it. Every
    # chunk id is an alias for one logical document, so locking this row would
    # let requests through different chunks deadlock during group replacement.
    result = await db.execute(
        select(KnowledgeStore).where(KnowledgeStore.id == doc_id)
    )
    addressed = result.scalar_one_or_none()
    if not addressed or addressed.namespace != namespace:
        raise HTTPException(404, f"Document {doc_id} not found in namespace {namespace}")

    # Resolve every alias to chunk zero and lock that persistent canonical row.
    # This gives concurrent edits one lock order and one stable public id.
    source_repo = KnowledgeRepository(session=db, org_id=addressed.organization_id)
    doc = await source_repo.lock_document(
        namespace, addressed.key, addressed.organization_id
    )
    if not doc:
        raise HTTPException(404, f"Document {doc_id} not found in namespace {namespace}")

    # Snapshot identity/audit fields before sibling replacement.
    doc_key = doc.key
    current_org_id = doc.organization_id
    original_created_at = doc.created_at
    original_created_by = doc.created_by
    metadata = data.metadata if data.metadata is not None else (doc.doc_metadata or {})

    # Resolve the target scope (defaults to the doc's current org when unchanged).
    target_org_id = current_org_id
    if scope is not None:
        from src.core.org_filter import resolve_target_org
        try:
            target_org_id = resolve_target_org(user, scope)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

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


@router.delete("/{namespace}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    namespace: str,
    doc_id: UUID,
    db: DbSession,
    user: CurrentSuperuser,
) -> None:
    """Delete a document — every chunk row of it, not just the addressed row."""
    result = await db.execute(
        select(KnowledgeStore).where(KnowledgeStore.id == doc_id)
    )
    doc = result.scalar_one_or_none()
    if not doc or doc.namespace != namespace:
        raise HTTPException(404, f"Document {doc_id} not found in namespace {namespace}")

    repo = KnowledgeRepository(session=db, org_id=doc.organization_id)
    await repo.delete_document(namespace, doc.key, doc.organization_id)


@router.delete("/{namespace}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_namespace(
    namespace: str,
    db: DbSession,
    user: CurrentSuperuser,
    scope: str | None = Query(default=None),
) -> None:
    """Delete all documents in a namespace."""
    from src.core.org_filter import resolve_target_org
    try:
        target_org_id = resolve_target_org(user, scope)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    repo = KnowledgeRepository(session=db, org_id=target_org_id)
    deleted = await repo.delete_namespace(namespace=namespace, organization_id=target_org_id)

    if deleted == 0:
        raise HTTPException(404, f"Namespace '{namespace}' not found or empty")

    # Also clean up any role assignments for this namespace
    await db.execute(
        delete(KnowledgeNamespaceRole).where(
            KnowledgeNamespaceRole.namespace == namespace
        )
    )
    await db.flush()
