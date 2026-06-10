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
from sqlalchemy import delete, or_, select, update

from src.core.auth import CurrentActiveUser, CurrentSuperuser
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.core.org_filter import OrgFilterType, resolve_org_filter
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


    try:
        filter_type, filter_org_id = resolve_org_filter(user, scope)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    stmt = select(KnowledgeStore)

    # Apply org scope filter (same pattern as workflows router)
    if filter_type == OrgFilterType.ALL:
        pass  # No org filter - show everything
    elif filter_type == OrgFilterType.GLOBAL_ONLY:
        stmt = stmt.where(KnowledgeStore.organization_id.is_(None))
    elif filter_type == OrgFilterType.ORG_ONLY:
        stmt = stmt.where(KnowledgeStore.organization_id == filter_org_id)
    else:  # ORG_PLUS_GLOBAL
        stmt = stmt.where(
            or_(
                KnowledgeStore.organization_id == filter_org_id,
                KnowledgeStore.organization_id.is_(None),
            )
        )

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


    try:
        filter_type, filter_org_id = resolve_org_filter(user, scope)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    stmt = select(KnowledgeStore).where(KnowledgeStore.namespace == namespace)

    if filter_type == OrgFilterType.ALL:
        pass
    elif filter_type == OrgFilterType.GLOBAL_ONLY:
        stmt = stmt.where(KnowledgeStore.organization_id.is_(None))
    elif filter_type == OrgFilterType.ORG_ONLY:
        stmt = stmt.where(KnowledgeStore.organization_id == filter_org_id)
    else:  # ORG_PLUS_GLOBAL
        stmt = stmt.where(
            or_(
                KnowledgeStore.organization_id == filter_org_id,
                KnowledgeStore.organization_id.is_(None),
            )
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
        content=doc.content,
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
    """Update a document and re-embed. Optionally change scope."""
    result = await db.execute(
        select(KnowledgeStore).where(KnowledgeStore.id == doc_id)
    )
    doc = result.scalar_one_or_none()
    if not doc or doc.namespace != namespace:
        raise HTTPException(404, f"Document {doc_id} not found in namespace {namespace}")

    # Re-embed
    try:
        from src.services.embeddings.factory import get_embedding_client
        client = await get_embedding_client(db)
        embedding = await client.embed(data.content)
    except ValueError as e:
        raise HTTPException(503, f"Embedding service unavailable: {e}")

    doc.content = data.content
    doc.embedding = embedding
    if data.metadata is not None:
        doc.doc_metadata = data.metadata
    doc.updated_at = datetime.now(timezone.utc)

    # Update scope if provided
    if scope is not None:
        from src.core.org_filter import resolve_target_org
        try:
            target_org_id = resolve_target_org(user, scope)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        # Pre-check for unique constraint conflict when changing scope
        if doc.key and target_org_id != doc.organization_id:
            conflict = await db.execute(
                select(KnowledgeStore.id).where(
                    KnowledgeStore.namespace == namespace,
                    KnowledgeStore.organization_id == target_org_id,
                    KnowledgeStore.key == doc.key,
                    KnowledgeStore.id != doc_id,
                )
            )
            conflicting_id = conflict.scalar_one_or_none()
            if conflicting_id:
                if replace:
                    await db.execute(
                        delete(KnowledgeStore).where(KnowledgeStore.id == conflicting_id)
                    )
                else:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "conflict",
                            "message": f"A document with key '{doc.key}' already exists in namespace '{namespace}' for the target scope",
                            "conflicting_id": str(conflicting_id),
                            "key": doc.key,
                            "namespace": namespace,
                        },
                    )

        doc.organization_id = target_org_id

    await db.flush()

    return KnowledgeDocumentPublic(
        id=str(doc.id),
        namespace=doc.namespace,
        key=doc.key,
        content=doc.content,
        metadata=doc.doc_metadata or {},
        organization_id=str(doc.organization_id) if doc.organization_id else None,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.delete("/{namespace}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    namespace: str,
    doc_id: UUID,
    db: DbSession,
    user: CurrentSuperuser,
) -> None:
    """Delete a document."""
    result = await db.execute(
        select(KnowledgeStore).where(KnowledgeStore.id == doc_id)
    )
    doc = result.scalar_one_or_none()
    if not doc or doc.namespace != namespace:
        raise HTTPException(404, f"Document {doc_id} not found in namespace {namespace}")

    await db.execute(
        delete(KnowledgeStore).where(KnowledgeStore.id == doc_id)
    )
    await db.flush()


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
