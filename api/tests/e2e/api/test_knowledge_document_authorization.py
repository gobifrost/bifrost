"""Live reach boundaries for document-by-ID Knowledge operations."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.e2e


async def _seed_documents(
    session_factory, *, organization_id: UUID, foreign_organization_id: UUID
) -> tuple[str, UUID, UUID]:
    """Commit synthetic rows directly so this boundary needs no embedding service."""
    from src.models.orm.knowledge import KnowledgeStore

    namespace = f"knowledge-auth-{uuid4().hex}"
    own = KnowledgeStore(
        id=uuid4(),
        namespace=namespace,
        organization_id=organization_id,
        key="contoso-guide",
        content="Contoso knowledge document",
        doc_metadata={},
        embedding=[0.1, 0.2, 0.3],
    )
    foreign = KnowledgeStore(
        id=uuid4(),
        namespace=namespace,
        organization_id=foreign_organization_id,
        key="fabrikam-guide",
        content="Fabrikam knowledge document",
        doc_metadata={},
        embedding=[0.1, 0.2, 0.3],
    )
    async with session_factory() as session:
        session.add_all([own, foreign])
        await session.commit()
    return namespace, own.id, foreign.id


async def _delete_documents(session_factory, document_ids: tuple[UUID, UUID]) -> None:
    from sqlalchemy import delete

    from src.models.orm.knowledge import KnowledgeStore

    async with session_factory() as session:
        await session.execute(delete(KnowledgeStore).where(KnowledgeStore.id.in_(document_ids)))
        await session.commit()


async def _set_role_permissions(
    session_factory, role_id: str, permissions: frozenset[str]
) -> None:
    from src.services.role_permissions import set_role_permissions

    async with session_factory() as session:
        await set_role_permissions(
            session,
            role_id=UUID(role_id),
            permissions=permissions,
        )
        await session.commit()


def test_document_mutations_conceal_foreign_ids(
    e2e_client,
    platform_admin,
    org1,
    org2,
    org1_user,
    async_session_factory,
) -> None:
    """Scoped write reach hides another organization's document UUIDs."""
    namespace, own_id, foreign_id = asyncio.run(
        _seed_documents(
            async_session_factory,
            organization_id=UUID(org1["id"]),
            foreign_organization_id=UUID(org2["id"]),
        )
    )
    role_id: str | None = None
    own_path = f"/api/knowledge-sources/{namespace}/documents/{own_id}"
    foreign_path = f"/api/knowledge-sources/{namespace}/documents/{foreign_id}"
    missing_path = f"/api/knowledge-sources/{namespace}/documents/{uuid4()}"

    try:
        own_get = e2e_client.get(own_path, headers=org1_user.headers)
        assert own_get.status_code == 200, own_get.text

        foreign_get = e2e_client.get(foreign_path, headers=org1_user.headers)
        assert foreign_get.status_code == 404, foreign_get.text

        for path in (foreign_path, missing_path):
            refused_update = e2e_client.put(
                path,
                headers=org1_user.headers,
                json={"content": "attempted update"},
            )
            assert refused_update.status_code == 403, refused_update.text

            refused_delete = e2e_client.delete(path, headers=org1_user.headers)
            assert refused_delete.status_code == 403, refused_delete.text

        role_response = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={"name": f"Contoso Knowledge Writer {uuid4().hex[:8]}"},
        )
        assert role_response.status_code == 201, role_response.text
        role_id = role_response.json()["id"]
        asyncio.run(
            _set_role_permissions(
                async_session_factory,
                role_id,
                frozenset({"knowledge.readwrite"}),
            )
        )
        writer_assignment = e2e_client.post(
            f"/api/roles/{role_id}/users",
            headers=platform_admin.headers,
            json={"user_ids": [str(org1_user.user_id)]},
        )
        assert writer_assignment.status_code == 204, writer_assignment.text

        writer_own_get = e2e_client.get(own_path, headers=org1_user.headers)
        assert writer_own_get.status_code == 200, writer_own_get.text

        for path in (foreign_path, missing_path):
            concealed_update = e2e_client.put(
                path,
                headers=org1_user.headers,
                json={"content": "attempted scoped update"},
            )
            assert concealed_update.status_code == 404, concealed_update.text

            concealed_delete = e2e_client.delete(path, headers=org1_user.headers)
            assert concealed_delete.status_code == 404, concealed_delete.text
    finally:
        if role_id is not None:
            e2e_client.delete(f"/api/roles/{role_id}", headers=platform_admin.headers)
        asyncio.run(_delete_documents(async_session_factory, (own_id, foreign_id)))
