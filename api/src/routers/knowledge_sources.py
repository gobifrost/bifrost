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
from sqlalchemy import delete, func, or_, select, update

from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
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
from src.models.orm.organizations import Organization
from src.models.orm.users import Role
from src.repositories.knowledge import KnowledgeRepository
from src.services.audit import emit_audit
from src.services.authorization import (
    AuthorizationBoundaryKind,
    AuthorizationContext,
    CurrentAuthorizationContext,
)
from src.services.operation_catalog import operation_route

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge-sources", tags=["Knowledge Sources"])


def _deny_external(authorization: AuthorizationContext) -> None:
    """403 an external principal off the direct knowledge surface.

    The knowledge store has no grant axis (no roles, no access_level, no row
    policies), so its read endpoints are implicitly internal-only. Externals
    reach KB content only THROUGH workflows/agents they were granted (the
    engine sentinel keeps the full cascade).
    """
    if authorization.requester.is_external:
        raise HTTPException(
            status_code=403,
            detail="External users cannot access the knowledge store directly",
        )


def _selected_knowledge_organization(
    authorization: AuthorizationContext,
) -> UUID | None:
    """Return the exact writable Knowledge boundary."""

    boundary = authorization.selected_boundary
    if boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select one organization or Global before changing Knowledge",
        )
    if boundary.kind is AuthorizationBoundaryKind.PLATFORM:
        return None
    return boundary.organization_id


def _knowledge_visibility_clause(authorization: AuthorizationContext):
    """Filter Knowledge rows to the explicitly selected read context."""

    boundary = authorization.selected_boundary
    if boundary.kind is AuthorizationBoundaryKind.PLATFORM:
        return KnowledgeStore.organization_id.is_(None)
    if boundary.kind is AuthorizationBoundaryKind.ORGANIZATION:
        return or_(
            KnowledgeStore.organization_id == boundary.organization_id,
            KnowledgeStore.organization_id.is_(None),
        )
    return KnowledgeStore.organization_id.in_(
        select(Organization.id).where(Organization.is_provider.is_(False))
    )


def _namespace_role_visibility_clause(authorization: AuthorizationContext):
    boundary = authorization.selected_boundary
    if boundary.kind is AuthorizationBoundaryKind.PLATFORM:
        return KnowledgeNamespaceRole.organization_id.is_(None)
    if boundary.kind is AuthorizationBoundaryKind.ORGANIZATION:
        return or_(
            KnowledgeNamespaceRole.organization_id == boundary.organization_id,
            KnowledgeNamespaceRole.organization_id.is_(None),
        )
    return KnowledgeNamespaceRole.organization_id.in_(
        select(Organization.id).where(Organization.is_provider.is_(False))
    )


async def _require_visible_knowledge_organization(
    db: DbSession,
    authorization: AuthorizationContext,
    organization_id: UUID | None,
) -> None:
    """Hide a Knowledge resource outside the selected read context."""

    if authorization.has_capability("platform.superuser"):
        return
    boundary = authorization.selected_boundary
    if organization_id is None:
        if boundary.kind in {
            AuthorizationBoundaryKind.PLATFORM,
            AuthorizationBoundaryKind.ORGANIZATION,
        }:
            return
    elif (
        boundary.kind is AuthorizationBoundaryKind.ORGANIZATION
        and boundary.organization_id == organization_id
    ):
        return
    elif boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
        is_provider = await db.scalar(
            select(Organization.is_provider).where(Organization.id == organization_id)
        )
        if is_provider is False:
            return
    raise HTTPException(status_code=404, detail="Knowledge resource not found")


def _require_knowledge_mutation(
    authorization: AuthorizationContext,
    organization_id: UUID | None,
) -> None:
    authorization.require("knowledge.readwrite")
    if (
        authorization.selected_boundary.kind
        is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select one organization or Global before changing Knowledge",
        )
    authorization.require_resource_boundary(organization_id)


# =============================================================================
# Namespace Listing
# =============================================================================


@router.get("", **operation_route("knowledge.namespaces.list"))
async def list_namespaces(
    db: DbSession,
    authorization: CurrentAuthorizationContext,
    scope: str | None = Query(default=None),
) -> list[KnowledgeNamespaceInfo]:
    """List knowledge namespaces derived from knowledge_store."""
    del scope  # The shared dependency validates and resolves the legacy selector.
    authorization.require("knowledge.read")
    _deny_external(authorization)
    rows = (
        await db.execute(
            select(
                KnowledgeStore.namespace,
                KnowledgeStore.organization_id,
                func.count(KnowledgeStore.id),
            )
            .where(_knowledge_visibility_clause(authorization))
            .group_by(KnowledgeStore.namespace, KnowledgeStore.organization_id)
        )
    ).all()
    counts: dict[str, dict[str, int]] = {}
    for namespace, organization_id, count in rows:
        bucket = counts.setdefault(namespace, {"global": 0, "org": 0})
        bucket["global" if organization_id is None else "org"] += count
    return [
        KnowledgeNamespaceInfo(
            namespace=namespace,
            document_count=scope_counts["global"] + scope_counts["org"],
            global_count=scope_counts["global"],
            org_count=scope_counts["org"],
        )
        for namespace, scope_counts in sorted(counts.items())
    ]


# =============================================================================
# Namespace Role Assignments
# (Must be registered before /{namespace} routes to avoid path conflicts)
# =============================================================================


@router.get("/roles")
async def list_namespace_roles(
    db: DbSession,
    authorization: CurrentAuthorizationContext,
) -> list[KnowledgeNamespaceRolePublic]:
    """List all namespace role assignments."""
    authorization.require("knowledge.read")
    _deny_external(authorization)
    result = await db.execute(
        select(KnowledgeNamespaceRole).where(
            _namespace_role_visibility_clause(authorization)
        )
    )
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
    authorization: CurrentAuthorizationContext,
) -> list[KnowledgeNamespaceRolePublic]:
    """Assign roles to a namespace."""
    requested_org_id = UUID(data.organization_id) if data.organization_id else None
    org_id = (
        requested_org_id
        if data.organization_id is not None
        else _selected_knowledge_organization(authorization)
    )
    _require_knowledge_mutation(authorization, org_id)
    created = []

    for role_id_str in data.role_ids:
        try:
            role_uuid = UUID(role_id_str)
        except ValueError:
            logger.warning(f"Invalid role ID: {log_safe(role_id_str)}")
            continue

        # Verify role exists
        result = await db.execute(select(Role).where(Role.id == role_uuid))
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
            assigned_by=authorization.effective_actor.email,
        )
        db.add(assignment)
        await db.flush()

        created.append(
            KnowledgeNamespaceRolePublic(
                id=str(assignment.id),
                namespace=assignment.namespace,
                organization_id=str(assignment.organization_id)
                if assignment.organization_id
                else None,
                role_id=str(assignment.role_id),
                assigned_by=assignment.assigned_by,
            )
        )

    if created:
        await emit_audit(
            db,
            "knowledge.namespace_roles.assign",
            resource_type="knowledge_namespace",
            details={
                "namespace": data.namespace,
                "organization_id": str(org_id) if org_id else None,
                "role_ids": [assignment.role_id for assignment in created],
            },
        )
    return created


@router.delete("/roles/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_namespace_role(
    assignment_id: UUID,
    db: DbSession,
    authorization: CurrentAuthorizationContext,
) -> None:
    """Remove a namespace role assignment."""
    result = await db.execute(
        select(KnowledgeNamespaceRole).where(KnowledgeNamespaceRole.id == assignment_id)
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(404, f"Assignment {assignment_id} not found")

    _require_knowledge_mutation(authorization, assignment.organization_id)

    await db.execute(
        delete(KnowledgeNamespaceRole).where(KnowledgeNamespaceRole.id == assignment_id)
    )
    await db.flush()
    await emit_audit(
        db,
        "knowledge.namespace_roles.remove",
        resource_type="knowledge_namespace",
        resource_id=assignment_id,
        details={
            "namespace": assignment.namespace,
            "organization_id": (
                str(assignment.organization_id) if assignment.organization_id else None
            ),
            "role_id": str(assignment.role_id),
        },
    )


# =============================================================================
# Document listing (all namespaces)
# (Must be registered before /{namespace} routes to avoid path conflicts)
# =============================================================================


@router.get("/documents", **operation_route("knowledge.documents.list"))
async def list_all_documents(
    db: DbSession,
    authorization: CurrentAuthorizationContext,
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
    del scope
    authorization.require("knowledge.read")
    _deny_external(authorization)

    stmt = select(KnowledgeStore).where(_knowledge_visibility_clause(authorization))

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
    authorization: CurrentAuthorizationContext,
) -> dict:
    """Bulk update scope for multiple documents.

    When replace=true in the request body, conflicting documents in the
    target scope are deleted before moving.
    """
    target_org_id = _selected_knowledge_organization(authorization)
    requested_scope = data.scope.strip().lower()
    if requested_scope == "global":
        requested_org_id = None
    else:
        try:
            requested_org_id = UUID(requested_scope)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="scope must be global or an organization UUID",
            ) from exc
    if requested_org_id != target_org_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The requested scope does not match the selected authorization boundary",
        )
    _require_knowledge_mutation(authorization, target_org_id)

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
    for source_doc in source_rows:
        _require_knowledge_mutation(authorization, source_doc.organization_id)
    keyed_docs = [
        d for d in source_rows if d.key and d.organization_id != target_org_id
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

    await emit_audit(
        db,
        "knowledge.documents.scope_update",
        resource_type="knowledge_document",
        details={
            "document_ids": [str(document_id) for document_id in doc_uuids],
            "organization_id": str(target_org_id) if target_org_id else None,
            "updated": result.rowcount,
        },
    )

    return {"updated": result.rowcount}


# =============================================================================
# Document CRUD (namespace-based paths)
# =============================================================================


@router.get("/{namespace}/documents")
async def list_documents(
    namespace: str,
    db: DbSession,
    authorization: CurrentAuthorizationContext,
    scope: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[KnowledgeDocumentSummary]:
    """List documents in a namespace."""
    del scope
    authorization.require("knowledge.read")
    _deny_external(authorization)

    stmt = select(KnowledgeStore).where(
        KnowledgeStore.namespace == namespace,
        _knowledge_visibility_clause(authorization),
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


@router.post(
    "/{namespace}/documents",
    status_code=status.HTTP_201_CREATED,
    **operation_route("knowledge.documents.create"),
)
async def create_document(
    namespace: str,
    data: KnowledgeDocumentCreate,
    db: DbSession,
    authorization: CurrentAuthorizationContext,
    scope: str | None = Query(default=None),
) -> KnowledgeDocumentPublic:
    """Create a document in a namespace with embedding."""
    del scope
    target_org_id = _selected_knowledge_organization(authorization)
    _require_knowledge_mutation(authorization, target_org_id)

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
        created_by=authorization.effective_actor.user_id,
        embedder=client,
    )
    await db.flush()

    # Load the created document
    result = await db.execute(
        select(KnowledgeStore).where(KnowledgeStore.id == UUID(doc_ids[0]))
    )
    doc = result.scalar_one()

    await emit_audit(
        db,
        "knowledge.document.create",
        resource_type="knowledge_document",
        resource_id=doc.id,
        details={
            "namespace": namespace,
            "key": doc.key,
            "organization_id": str(target_org_id) if target_org_id else None,
        },
    )

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


@router.get(
    "/{namespace}/documents/{doc_id}",
    **operation_route("knowledge.documents.get"),
)
async def get_document(
    namespace: str,
    doc_id: UUID,
    db: DbSession,
    authorization: CurrentAuthorizationContext,
) -> KnowledgeDocumentPublic:
    """Get a document by UUID."""
    authorization.require("knowledge.read")
    _deny_external(authorization)
    repo = KnowledgeRepository(session=db, org_id=None)
    doc = await repo.get_by_id(doc_id)

    if not doc or doc.namespace != namespace:
        raise HTTPException(
            404, f"Document {doc_id} not found in namespace {namespace}"
        )
    await _require_visible_knowledge_organization(
        db,
        authorization,
        UUID(doc.organization_id) if doc.organization_id else None,
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


@router.put(
    "/{namespace}/documents/{doc_id}",
    **operation_route("knowledge.documents.update"),
)
async def update_document(
    namespace: str,
    doc_id: UUID,
    data: KnowledgeDocumentUpdate,
    db: DbSession,
    authorization: CurrentAuthorizationContext,
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
    authorization.require("knowledge.readwrite")
    # First resolve the addressed physical chunk without locking it. Every
    # chunk id is an alias for one logical document, so locking this row would
    # let requests through different chunks deadlock during group replacement.
    result = await db.execute(select(KnowledgeStore).where(KnowledgeStore.id == doc_id))
    addressed = result.scalar_one_or_none()
    if not addressed or addressed.namespace != namespace:
        raise HTTPException(
            404, f"Document {doc_id} not found in namespace {namespace}"
        )
    _require_knowledge_mutation(authorization, addressed.organization_id)

    # Resolve every alias to chunk zero and lock that persistent canonical row.
    # This gives concurrent edits one lock order and one stable public id.
    source_repo = KnowledgeRepository(session=db, org_id=addressed.organization_id)
    doc = await source_repo.lock_document(
        namespace, addressed.key, addressed.organization_id
    )
    if not doc:
        raise HTTPException(
            404, f"Document {doc_id} not found in namespace {namespace}"
        )

    # Snapshot identity/audit fields before sibling replacement.
    doc_key = doc.key
    current_org_id = doc.organization_id
    original_created_at = doc.created_at
    original_created_by = doc.created_by
    metadata = data.metadata if data.metadata is not None else (doc.doc_metadata or {})

    # Resolve the target scope (defaults to the doc's current org when unchanged).
    target_org_id = _selected_knowledge_organization(authorization)
    if scope is None and target_org_id != current_org_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select the document's exact authorization boundary before editing it",
        )
    _require_knowledge_mutation(authorization, target_org_id)

    repo = KnowledgeRepository(session=db, org_id=target_org_id)

    if target_org_id != current_org_id:
        # Moving scope can collide with a document already holding this
        # identity in the target scope — keyed or keyless (NULL keys are
        # equal under the NULLS NOT DISTINCT unique constraint). 409 before
        # mutating anything, unless the caller asked to replace it.
        conflicting_id = await repo.find_document_id(namespace, doc_key, target_org_id)
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

    await emit_audit(
        db,
        "knowledge.document.update",
        resource_type="knowledge_document",
        resource_id=new_doc.id,
        details={
            "namespace": namespace,
            "key": new_doc.key,
            "organization_id": str(target_org_id) if target_org_id else None,
            "moved": target_org_id != current_org_id,
        },
    )

    return KnowledgeDocumentPublic(
        id=str(new_doc.id),
        namespace=new_doc.namespace,
        key=new_doc.key,
        # Echo the full submitted content, not the first chunk row's slice.
        content=data.content,
        metadata=new_doc.doc_metadata or {},
        organization_id=str(new_doc.organization_id)
        if new_doc.organization_id
        else None,
        created_at=new_doc.created_at,
        updated_at=new_doc.updated_at,
    )


@router.delete(
    "/{namespace}/documents/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    **operation_route("knowledge.documents.delete"),
)
async def delete_document(
    namespace: str,
    doc_id: UUID,
    db: DbSession,
    authorization: CurrentAuthorizationContext,
) -> None:
    """Delete a document — every chunk row of it, not just the addressed row."""
    result = await db.execute(select(KnowledgeStore).where(KnowledgeStore.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc or doc.namespace != namespace:
        raise HTTPException(
            404, f"Document {doc_id} not found in namespace {namespace}"
        )
    _require_knowledge_mutation(authorization, doc.organization_id)

    repo = KnowledgeRepository(session=db, org_id=doc.organization_id)
    await repo.delete_document(namespace, doc.key, doc.organization_id)
    await emit_audit(
        db,
        "knowledge.document.delete",
        resource_type="knowledge_document",
        resource_id=doc_id,
        details={
            "namespace": namespace,
            "key": doc.key,
            "organization_id": (
                str(doc.organization_id) if doc.organization_id else None
            ),
        },
    )


@router.delete("/{namespace}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_namespace(
    namespace: str,
    db: DbSession,
    authorization: CurrentAuthorizationContext,
    scope: str | None = Query(default=None),
) -> None:
    """Delete all documents in a namespace."""
    del scope
    target_org_id = _selected_knowledge_organization(authorization)
    _require_knowledge_mutation(authorization, target_org_id)

    repo = KnowledgeRepository(session=db, org_id=target_org_id)
    deleted = await repo.delete_namespace(
        namespace=namespace, organization_id=target_org_id
    )

    if deleted == 0:
        raise HTTPException(404, f"Namespace '{namespace}' not found or empty")

    # Also clean up any role assignments for this namespace
    await db.execute(
        delete(KnowledgeNamespaceRole).where(
            KnowledgeNamespaceRole.namespace == namespace
        )
    )
    await db.flush()
    await emit_audit(
        db,
        "knowledge.namespace.delete",
        resource_type="knowledge_namespace",
        details={
            "namespace": namespace,
            "organization_id": str(target_org_id) if target_org_id else None,
            "document_count": deleted,
        },
    )
