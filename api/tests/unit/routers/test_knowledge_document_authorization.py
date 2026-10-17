"""Authorization boundaries for a knowledge document UUID lookup."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.knowledge import KnowledgeDocumentUpdate
from src.repositories.knowledge import KnowledgeDocument, KnowledgeRepository
from src.routers import knowledge_sources as router
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller


CONTOSO = UUID("11111111-1111-1111-1111-111111111111")
FABRIKAM = UUID("22222222-2222-2222-2222-222222222222")
NAMESPACE = "support"


def _caller(*, home_organization_id: UUID, permissions: frozenset[str]) -> Caller:
    user_id = uuid4()
    principal = UserPrincipal(
        user_id=user_id,
        email="reader@example.com",
        organization_id=home_organization_id,
        is_verified=True,
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=user_id,
            home_organization_id=home_organization_id,
            base_role_id=uuid4(),
            is_external=False,
            base_permissions=permissions,
        ),
    )


def _document(*, organization_id: UUID | None, namespace: str = NAMESPACE) -> KnowledgeDocument:
    return KnowledgeDocument(
        id=str(uuid4()),
        namespace=namespace,
        key="guide",
        content="Helpful documentation",
        metadata={"category": "support"},
        organization_id=str(organization_id) if organization_id else None,
        created_at=datetime.now(timezone.utc),
    )


def _patch_document_lookup(monkeypatch, caller: Caller, document: KnowledgeDocument | None):
    get_by_id = AsyncMock(return_value=document)
    monkeypatch.setattr(router, "_knowledge_caller", AsyncMock(return_value=caller))
    monkeypatch.setattr(KnowledgeRepository, "get_by_id", get_by_id)
    return get_by_id


def _stored_document(*, organization_id: UUID | None, namespace: str = NAMESPACE):
    return SimpleNamespace(
        id=uuid4(),
        namespace=namespace,
        key="guide",
        organization_id=organization_id,
        doc_metadata={"category": "support"},
        created_at=datetime.now(timezone.utc),
        created_by=uuid4(),
    )


def _document_db(document):
    return SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalar_one_or_none=MagicMock(return_value=document)
            )
        )
    )


async def _mutate_document(operation: str, *, doc_id: UUID, db, user: UserPrincipal):
    if operation == "update":
        return await router.update_document(
            NAMESPACE,
            doc_id,
            KnowledgeDocumentUpdate(content="Updated documentation"),
            db,
            user,
            scope=None,
            replace=False,
        )
    return await router.delete_document(NAMESPACE, doc_id, db, user)


@pytest.mark.asyncio
async def test_document_get_returns_own_reachable_document(monkeypatch):
    caller = _caller(home_organization_id=FABRIKAM, permissions=frozenset({"knowledge.read"}))
    document = _document(organization_id=FABRIKAM)
    get_by_id = _patch_document_lookup(monkeypatch, caller, document)

    result = await router.get_document(
        NAMESPACE, UUID(document.id), SimpleNamespace(), caller.principal
    )

    assert result.id == document.id
    assert result.content == document.content
    get_by_id.assert_awaited_once_with(UUID(document.id))


@pytest.mark.asyncio
async def test_document_get_masks_foreign_document_outside_permission_reach(monkeypatch):
    caller = _caller(home_organization_id=FABRIKAM, permissions=frozenset({"knowledge.read"}))
    document = _document(organization_id=CONTOSO)
    get_by_id = _patch_document_lookup(monkeypatch, caller, document)

    with pytest.raises(HTTPException) as exc:
        await router.get_document(
            NAMESPACE, UUID(document.id), SimpleNamespace(), caller.principal
        )

    assert exc.value.status_code == 404
    get_by_id.assert_awaited_once_with(UUID(document.id))


@pytest.mark.asyncio
async def test_document_get_without_named_permission_is_forbidden_before_lookup(monkeypatch):
    caller = _caller(home_organization_id=FABRIKAM, permissions=frozenset())
    get_by_id = _patch_document_lookup(monkeypatch, caller, None)

    with pytest.raises(HTTPException) as exc:
        await router.get_document(NAMESPACE, uuid4(), SimpleNamespace(), caller.principal)

    assert exc.value.status_code == 403
    get_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_document_get_wrong_namespace_is_not_found(monkeypatch):
    caller = _caller(home_organization_id=FABRIKAM, permissions=frozenset({"knowledge.read"}))
    document = _document(organization_id=FABRIKAM, namespace="operations")
    get_by_id = _patch_document_lookup(monkeypatch, caller, document)

    with pytest.raises(HTTPException) as exc:
        await router.get_document(
            NAMESPACE, UUID(document.id), SimpleNamespace(), caller.principal
        )

    assert exc.value.status_code == 404
    get_by_id.assert_awaited_once_with(UUID(document.id))


@pytest.mark.asyncio
async def test_document_get_global_requires_global_knowledge_permission(monkeypatch):
    caller = _caller(home_organization_id=FABRIKAM, permissions=frozenset({"knowledge.read"}))
    document = _document(organization_id=None)
    get_by_id = _patch_document_lookup(monkeypatch, caller, document)

    with pytest.raises(HTTPException) as exc:
        await router.get_document(
            NAMESPACE, UUID(document.id), SimpleNamespace(), caller.principal
        )

    assert exc.value.status_code == 403
    get_by_id.assert_awaited_once_with(UUID(document.id))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_document_mutation_without_named_permission_is_forbidden_before_lookup(
    monkeypatch, operation
):
    caller = _caller(home_organization_id=FABRIKAM, permissions=frozenset())
    db = _document_db(None)
    monkeypatch.setattr(router, "_knowledge_caller", AsyncMock(return_value=caller))

    with pytest.raises(HTTPException) as exc:
        await _mutate_document(operation, doc_id=uuid4(), db=db, user=caller.principal)

    assert exc.value.status_code == 403
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_document_mutation_masks_foreign_document_outside_permission_reach(
    monkeypatch, operation
):
    caller = _caller(
        home_organization_id=FABRIKAM,
        permissions=frozenset({"knowledge.readwrite"}),
    )
    document = _stored_document(organization_id=CONTOSO)
    db = _document_db(document)
    lock_document = AsyncMock()
    monkeypatch.setattr(router, "_knowledge_caller", AsyncMock(return_value=caller))
    monkeypatch.setattr(KnowledgeRepository, "lock_document", lock_document)

    with pytest.raises(HTTPException) as exc:
        await _mutate_document(operation, doc_id=document.id, db=db, user=caller.principal)

    assert exc.value.status_code == 404
    db.execute.assert_awaited_once()
    lock_document.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_document_mutation_global_document_requires_global_permission(
    monkeypatch, operation
):
    caller = _caller(
        home_organization_id=FABRIKAM,
        permissions=frozenset({"knowledge.readwrite"}),
    )
    document = _stored_document(organization_id=None)
    db = _document_db(document)
    monkeypatch.setattr(router, "_knowledge_caller", AsyncMock(return_value=caller))
    lock_document = AsyncMock(return_value=document)
    monkeypatch.setattr(KnowledgeRepository, "lock_document", lock_document)

    with pytest.raises(HTTPException) as exc:
        await _mutate_document(operation, doc_id=document.id, db=db, user=caller.principal)

    assert exc.value.status_code == 403
    db.execute.assert_awaited_once()
    if operation == "update":
        lock_document.assert_awaited_once_with(NAMESPACE, document.key, None)
    else:
        lock_document.assert_not_awaited()
